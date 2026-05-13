"""
bm25_rag.py  --  BM25 retriever for Phase 1 inference evaluation
=================================================================
Standalone retrieval module.  Imported by enhanced_inference.py.

Design decisions from 4-LLM expert consensus (May 2026):
  - BM25 is preferred over dense/semantic retrieval for short medical
    keyword queries: exact matches on "tourniquet", "epinephrine",
    "cardiac arrest" outperform semantic similarity, which risks
    returning "semantically close but clinically wrong" examples.
  - Hard 1-example limit: mobile latency budget (~20-30 s) does not
    allow the quadratic attention cost of multiple prepended examples.
  - 150-token cap per retrieved example (~115 words at 1.3 tok/word).
  - Gap-question gate: retrieval is SKIPPED ENTIRELY for Q6, Q17, Q21,
    Q22, Q28 (the 5 confirmed training-data protocol gaps).  For these
    questions the KB contains plausible-but-wrong examples that would
    actively worsen the answer.  Q21 (infant choking) and Q22 (embedded
    object) are specifically dangerous if retrieval fires.

Usage (as a module in enhanced_inference.py):
    from bm25_rag import BM25Retriever
    retriever = BM25Retriever("splits/10cat/train.json")
    result    = retriever.retrieve(question_id=17, query="treatment for shock")
    # result is None when gap-gated or below threshold
    # result is a dict with bm25_fired / bm25_skipped_gap when it fires

Usage (standalone diagnostic / smoke-test):
    python bm25_rag.py
    python bm25_rag.py --question "How do you treat cardiac arrest?"
    python bm25_rag.py --question_id 17 --question "Treatment for shock?"
    python bm25_rag.py --question_id 21 --question "Infant choking under 1 year"
    python bm25_rag.py --train_path splits/10cat/train.json --top_n 3
"""

import argparse
import json
import os
import sys

# ---------------------------------------------------------------------------
# Gap-question gate
# ---------------------------------------------------------------------------

#: Question IDs for which retrieval must be skipped completely.
#: These are the 5 confirmed training-data protocol gaps.
#: The KB contains the nearest plausible wrong answer for these questions.
#: Q21 (infant choking) and Q22 (embedded object) are actively dangerous.
GAP_QUESTION_IDS: frozenset = frozenset({6, 17, 21, 22, 28})

# Human-readable descriptions for logging
_GAP_DESCRIPTIONS = {
    6:  "Arterial bleeding / tourniquet placement",
    17: "Shock position (lay flat, elevate legs)",
    21: "Infant choking (back-blow / chest-thrust variant)",
    22: "Embedded object ('do not remove, stabilise')",
    28: "Helmet removal spinal immobilisation protocol",
}

# ---------------------------------------------------------------------------
# Token-cap constants
# ---------------------------------------------------------------------------

#: Maximum number of tokens allowed in a single retrieved example.
#: Enforced by word-count proxy (no tokenizer needed in this module).
RETRIEVED_TOKEN_CAP = 150

#: Empirical tokens-per-word ratio for medical first-aid prose (from data.py).
TOKENS_PER_WORD = 1.3

#: Derived word cap: floor(150 / 1.3) = 115
_WORD_CAP = int(RETRIEVED_TOKEN_CAP / TOKENS_PER_WORD)


# ---------------------------------------------------------------------------
# BM25Retriever
# ---------------------------------------------------------------------------

class BM25Retriever:
    """
    BM25 retriever over splits/10cat/train.json.

    Parameters
    ----------
    train_path : str
        Path to the training split JSON (list of {question, answer, category}).
    gap_gate   : bool
        If True (default), skip retrieval for GAP_QUESTION_IDS.
    verbose    : bool
        Print initialisation and per-query log lines.

    Public methods
    --------------
    retrieve(question_id, query) -> dict | None
        Returns a result dict or None (gap-gated / below threshold / unavailable).

    Result dict schema
    ------------------
    {
        "question"          : str,   # retrieved training question
        "answer"            : str,   # retrieved training answer (capped)
        "answer_full"       : str,   # original uncapped answer
        "category"          : str,
        "score"             : float, # normalised BM25 score (0-1)
        "bm25_fired"        : True,
        "bm25_skipped_gap"  : False,
        "word_cap_applied"  : bool,
    }

    When retrieval is skipped (gap gate or unavailable):
    {
        "bm25_fired"        : False,
        "bm25_skipped_gap"  : bool,  # True if gap gate, False if unavailable
    }
    """

    def __init__(
        self,
        train_path: str = "splits/10cat/train.json",
        gap_gate:   bool = True,
        verbose:    bool = True,
    ):
        self.gap_gate  = gap_gate
        self.verbose   = verbose
        self.available = False

        # Resolve path relative to this script's directory
        if not os.path.isabs(train_path):
            here = os.path.dirname(os.path.abspath(__file__))
            train_path = os.path.join(here, train_path)

        self._train_path = train_path

        if not os.path.exists(train_path):
            print(
                f"[BM25RAG] ERROR: train.json not found at {train_path}\n"
                "          Run data.py first to generate the training split."
            )
            return

        self._questions:  list = []
        self._answers:    list = []
        self._categories: list = []
        self._chunks:     list = []  # tokenised for BM25

        self._load_kb()
        self._build_index()

    # ------------------------------------------------------------------
    # Initialisation helpers
    # ------------------------------------------------------------------

    def _load_kb(self):
        """Load train.json and populate internal lists."""
        try:
            with open(self._train_path, encoding="utf-8") as f:
                samples = json.load(f)
        except Exception as e:
            print(f"[BM25RAG] ERROR: could not load train.json: {e}")
            return

        if not isinstance(samples, list) or not samples:
            print("[BM25RAG] ERROR: train.json is empty or not a list.")
            return

        for s in samples:
            if not isinstance(s, dict):
                continue
            q   = s.get("question", "").strip()
            a   = s.get("answer",   "").strip()
            cat = s.get("category", "General first aid").strip()
            if not q or not a:
                continue
            self._questions.append(q)
            self._answers.append(a)
            self._categories.append(cat)
            # Chunk used for BM25 scoring: category + question + answer
            self._chunks.append(f"{cat} {q} {a}".lower())

        if self.verbose:
            print(
                f"[BM25RAG] KB loaded: {len(self._questions):,} Q&A pairs "
                f"from {os.path.basename(self._train_path)}"
            )

    def _build_index(self):
        """Build BM25Okapi index over tokenised chunks."""
        if not self._chunks:
            return
        try:
            from rank_bm25 import BM25Okapi
        except ImportError:
            print(
                "[BM25RAG] rank_bm25 not installed.\n"
                "          pip install rank_bm25 --break-system-packages"
            )
            return

        tokenized = [chunk.split() for chunk in self._chunks]
        self._index   = BM25Okapi(tokenized)
        self.available = True
        if self.verbose:
            print(f"[BM25RAG] BM25 index ready ({len(tokenized):,} documents).")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def retrieve(self, question_id: int, query: str) -> dict:
        """
        Retrieve the single best-matching training example for *query*.

        Parameters
        ----------
        question_id : int
            The eval question ID (from eval_questions_40.json).
            Used exclusively for gap-gate checking.
        query : str
            The raw question text from the eval bank.

        Returns
        -------
        dict
            Always returns a dict.  Check ``result["bm25_fired"]`` to know
            whether a retrieved example is present.
        """
        # --- Gap gate ---------------------------------------------------------
        if self.gap_gate and question_id in GAP_QUESTION_IDS:
            if self.verbose:
                desc = _GAP_DESCRIPTIONS.get(question_id, "unknown gap")
                print(
                    f"[BM25RAG] Q{question_id:02d} GAP GATE — retrieval skipped. "
                    f"({desc})"
                )
            return {"bm25_fired": False, "bm25_skipped_gap": True}

        # --- Unavailable guard -----------------------------------------------
        if not self.available:
            return {"bm25_fired": False, "bm25_skipped_gap": False}

        # --- BM25 scoring -----------------------------------------------------
        import numpy as np

        query_tokens = query.lower().split()
        raw_scores   = self._index.get_scores(query_tokens)

        best_idx   = int(np.argmax(raw_scores))
        best_score = float(raw_scores[best_idx])
        max_score  = float(raw_scores.max())

        # Normalise to [0, 1]
        norm_score = (best_score / max_score) if max_score > 0 else 0.0

        if self.verbose:
            q_preview = query[:55] + "..." if len(query) > 55 else query
            print(
                f"[BM25RAG] Q{question_id:02d}  score={norm_score:.3f}  "
                f"retrieved: {self._questions[best_idx][:55]}..."
            )

        # --- Token cap --------------------------------------------------------
        answer_full    = self._answers[best_idx]
        answer_words   = answer_full.split()
        cap_applied    = len(answer_words) > _WORD_CAP
        answer_capped  = " ".join(answer_words[:_WORD_CAP]) if cap_applied else answer_full

        return {
            "question":         self._questions[best_idx],
            "answer":           answer_capped,
            "answer_full":      answer_full,
            "category":         self._categories[best_idx],
            "score":            round(norm_score, 4),
            "bm25_fired":       True,
            "bm25_skipped_gap": False,
            "word_cap_applied": cap_applied,
        }

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------

    def diagnose(self, question_id: int, query: str, top_n: int = 3):
        """
        Print top_n BM25 results without the gap gate.
        For manual inspection of what BM25 would retrieve.
        """
        if not self.available:
            print("[BM25RAG] Retriever not available.")
            return

        import numpy as np

        query_tokens = query.lower().split()
        raw_scores   = self._index.get_scores(query_tokens)
        max_score    = float(raw_scores.max()) if raw_scores.max() > 0 else 1.0
        top_indices  = np.argsort(raw_scores)[::-1][:top_n]

        gated = question_id in GAP_QUESTION_IDS
        print(f"\n{'='*60}")
        print(f"  BM25 Diagnose  Q{question_id:02d}")
        print(f"  Query  : {query[:70]}")
        print(f"  Gated  : {'YES — gap question (retrieval would be skipped)' if gated else 'no'}")
        print(f"{'='*60}")
        for rank, idx in enumerate(top_indices, 1):
            score = float(raw_scores[idx]) / max_score
            words = len(self._answers[idx].split())
            capped = words > _WORD_CAP
            print(f"\n  Rank {rank}  score={score:.3f}  [{self._categories[idx]}]"
                  f"  ({words}w{'  CAP APPLIED' if capped else ''})")
            print(f"  Q: {self._questions[idx][:80]}")
            print(f"  A: {self._answers[idx][:120]}{'...' if len(self._answers[idx]) > 120 else ''}")
        print()


# ---------------------------------------------------------------------------
# Standalone entry point (smoke-test / diagnostic)
# ---------------------------------------------------------------------------

def _parse_args():
    p = argparse.ArgumentParser(
        description="BM25 retriever diagnostic — Phase 1 RAG",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--train_path",   default="splits/10cat/train.json",
                   help="Path to training split (default: splits/10cat/train.json)")
    p.add_argument("--question_id",  type=int, default=0,
                   help="Eval question ID for gap-gate testing (default: 0 = no gate)")
    p.add_argument("--question",     default="",
                   help="Question text to retrieve for.  If omitted, runs smoke-test suite.")
    p.add_argument("--top_n",        type=int, default=3,
                   help="Number of results to show in diagnose mode (default: 3)")
    p.add_argument("--no_gap_gate",  action="store_true",
                   help="Disable the gap-question gate for this diagnostic run")
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()

    retriever = BM25Retriever(
        train_path = args.train_path,
        gap_gate   = not args.no_gap_gate,
        verbose    = True,
    )

    if not retriever.available:
        print("[BM25RAG] Retriever initialisation failed. Exiting.")
        sys.exit(1)

    if args.question:
        # Single query — show both retrieve() result and diagnose()
        print("\n--- retrieve() result ---")
        result = retriever.retrieve(args.question_id, args.question)
        print(json.dumps(result, indent=2, ensure_ascii=False))

        print("\n--- diagnose() (gap gate bypassed) ---")
        retriever.diagnose(args.question_id, args.question, top_n=args.top_n)

    else:
        # Smoke-test: run a representative sample from each category
        SMOKE_TESTS = [
            # (question_id, question_text, description)
            (1,  "How do you perform CPR on an adult?",                   "CPR — should fire"),
            (5,  "What are the signs and treatment for anaphylaxis?",     "Anaphylaxis — should fire"),
            (6,  "How do you control severe arterial bleeding?",          "GAP Q6 — should be skipped"),
            (17, "What position for a patient in shock?",                 "GAP Q17 — should be skipped"),
            (21, "How do you help a choking infant under 1 year?",        "GAP Q21 — should be skipped"),
            (22, "Glass embedded in thigh — what do you do?",             "GAP Q22 — should be skipped"),
            (28, "Motorcyclist crash, suspected spinal injury, helmet on", "GAP Q28 — should be skipped"),
            (10, "How do you treat a severe burn?",                       "Burns — should fire"),
            (15, "Signs and treatment of heat stroke?",                   "Heat stroke — should fire"),
        ]

        print("\n" + "=" * 60)
        print("  BM25 RAG Smoke Test — Phase 1")
        print("=" * 60)

        fired = 0
        gated = 0
        for qid, qtext, desc in SMOKE_TESTS:
            result = retriever.retrieve(qid, qtext)
            if result["bm25_fired"]:
                fired += 1
                print(f"  Q{qid:02d} FIRED   score={result['score']:.3f}  "
                      f"cap={'Y' if result['word_cap_applied'] else 'N'}  "
                      f"[{result['category']}]  — {desc}")
            elif result["bm25_skipped_gap"]:
                gated += 1
                print(f"  Q{qid:02d} GATED   (gap question)                              "
                      f"— {desc}")
            else:
                print(f"  Q{qid:02d} SKIP    (retriever unavailable)                      "
                      f"— {desc}")

        print(f"\n  Fired: {fired}  |  Gap-gated: {gated}  |  Total: {len(SMOKE_TESTS)}")
        print("=" * 60)
        print("\nRun with --question to test a specific query.")
        print("Run with --question_id N --question '...' to test gap-gate behaviour.")
