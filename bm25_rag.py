"""
bm25_rag.py  --  BM25 retriever for Phase 1 inference evaluation
=================================================================
Standalone retrieval module.  Imported by enhanced_inference.py and
v2_comprehensive_eval.py.

Design decisions from 4-LLM expert consensus (May 2026):
  - BM25 is preferred over dense/semantic retrieval for short medical
    keyword queries: exact matches on "tourniquet", "epinephrine",
    "cardiac arrest" outperform semantic similarity, which risks
    returning "semantically close but clinically wrong" examples.
  - Hard 1-example limit: mobile latency budget (~20-30 s) does not
    allow the quadratic attention cost of multiple prepended examples.
  - 150-token cap per retrieved example (~115 words at 1.3 tok/word).
  - Topic gate (replaces old ID gate from pre-v3): retrieval is SKIPPED
    ENTIRELY when the query text matches any confirmed corpus-gap regex.
    For these topics the KB contains plausible-but-wrong examples that
    would actively worsen the answer.  The gate fires on query TEXT so
    it works regardless of how question IDs are formatted in the bank.

Gap-gate change history
-----------------------
  v2 eval (June 2026): ID-keyed gate GAP_QUESTION_IDS = {6,17,21,22,28}
    existed in bm25_rag.py but was NEVER applied -- v2_comprehensive_eval.py
    used its own inline BM25Retriever with no gate.  audit_gap_gate.py
    (July 2026) confirmed all 41 Config F answers received top-3 ungated
    retrieval.  Secondary finding: even if the ID gate had been applied,
    the IDs map to v2-bank questions about fever, fracture signs, and
    fainting -- none of which are corpus gaps.  Gate replaced with topic
    patterns covering V2_PIPELINE corpus-audit findings and T4/T6 synthesis
    results.  See evaluations/v2_comprehensive_20260606_200713/audit_gap_gate.txt.

Usage (as a module in v2_comprehensive_eval.py or enhanced_inference.py):
    from bm25_rag import BM25Retriever
    retriever = BM25Retriever("splits/10cat/train.json", verbose=False)
    result    = retriever.retrieve("How do I treat a choking infant?")
    # result["bm25_fired"] is True when a retrieved example is present
    # result["bm25_skipped_gap"] is True when the topic gate fired

Usage (standalone diagnostic / smoke-test):
    python bm25_rag.py
    python bm25_rag.py --question "How do you treat cardiac arrest?"
    python bm25_rag.py --question "How do you help a choking infant?"
    python bm25_rag.py --train_path splits/10cat/train.json --top_n 3
"""

import argparse
import json
import os
import re
import sys

# ---------------------------------------------------------------------------
# Topic-based gap gate
# ---------------------------------------------------------------------------
# Each entry maps a topic key to (compiled_regex, corpus_audit_justification).
# The justification comment references V2_PIPELINE.md corpus counts or
# T4/T6 synthesis results from evaluations/v2_comprehensive_20260606_200713/.
# When ANY pattern matches the incoming query string, retrieval is skipped.
# ---------------------------------------------------------------------------

GAP_TOPIC_PATTERNS: dict = {

    # V2_PIPELINE corpus audit (run 2026-05-05, n=5,550 training records):
    # arterial+tourniquet = 2 / 5,550 records; BOTH examples discourage
    # tourniquet placement.  Retrieval injects anti-tourniquet advice for
    # any arterial-bleeding or snake-bite-tourniquet query.
    "tourniquet_escalation": (
        re.compile(r"tourniquet", re.I),
        "V2_PIPELINE audit: arterial+tourniquet 2/5550, both discouraging"
    ),

    # V2_PIPELINE audit: choking_heimlich_only = 41 records vs
    # choking_back_blows = 12.  Heimlich-dominant KB will reinforce wrong
    # technique for infants under 1 year (back blows + chest thrusts only).
    # T4/T6 synthesis: old-bank Q21 scored <=2/5 across all 6 configs.
    "infant_choking": (
        re.compile(r"(infant|baby).{0,40}chok|chok.{0,40}(infant|baby)", re.I),
        "V2_PIPELINE audit: choking_heimlich_only 41 vs back_blows 12"
    ),

    # T4/T6 synthesis: old-bank Q29 (spinal log-roll) scored <=2/5 across
    # all configs.  KB has 148 spinal records but the multi-rescuer log-roll
    # protocol is near-absent; retrieval injects generic spine-protection
    # advice that omits the log-roll mandate.
    "spinal_logroll": (
        re.compile(r"log.?roll|spinal.{0,30}(move|turn|transport|shift|roll)", re.I),
        "T4/T6 synthesis: spinal log-roll scored <=2/5 all configs"
    ),

    # T4/T6 synthesis: old-bank Q36 (vented chest seal) scored <=2/5 across
    # all configs.  KB penetrating-chest examples do not distinguish 3-sided
    # (non-occlusive) seal from 4-sided occlusive seal; retrieval injects
    # wrong seal advice.
    "chest_seal": (
        re.compile(r"chest seal|sucking chest|open chest wound", re.I),
        "T4/T6 synthesis: vented chest seal scored <=2/5 all configs"
    ),

    # T4/T6 synthesis: old-bank Q25 (naloxone) scored <=2/5 across all
    # configs.  KB has 260 poisoning records; naloxone protocol is
    # near-absent, retrieval injects general poisoning management.
    "naloxone_opioid": (
        re.compile(r"naloxone|opioid.{0,20}overdose|overdose.{0,20}opioid", re.I),
        "T4/T6 synthesis: naloxone/opioid scored <=2/5 all configs"
    ),

    # T4/T6 synthesis: old-bank Q33 (paediatric drowning CPR) scored <=2/5.
    # KB CPR records rarely include the 5-rescue-breath-first drowning
    # protocol for children; retrieval reinforces adult-first-compression
    # sequence.
    "rescue_breaths_drowning": (
        re.compile(
            r"rescue breath.{0,30}(child|drown|water)|drown.{0,30}(child|rescue)",
            re.I
        ),
        "T4/T6 synthesis: paediatric drowning CPR scored <=2/5 all configs"
    ),

    # v2 bank evaluation (DeepSeek panel, June 2026): V2Q37 (burn cooling
    # 20-min rule) was top-ranked training gap -- scored 0-1/5 across all
    # configs.  KB burn records specify incorrect durations or ice application.
    "burn_cooling": (
        re.compile(r"burn.{0,40}cool|cool.{0,40}burn", re.I),
        "DeepSeek eval: V2Q37 burn cooling top-ranked gap, scored 0-1/5"
    ),
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
        If True (default), apply GAP_TOPIC_PATTERNS to skip retrieval for
        confirmed corpus-gap topics.
    verbose    : bool
        Print initialisation and per-query log lines.

    Public methods
    --------------
    retrieve(query, question_id=None) -> dict
        Returns a result dict.  Check result["bm25_fired"] to determine
        whether a retrieved example is present.

    Result dict schema (when bm25_fired is True)
    ---------------------------------------------
    {
        "question"          : str,   # retrieved training question
        "answer"            : str,   # retrieved training answer (capped)
        "answer_full"       : str,   # original uncapped answer
        "category"          : str,
        "score"             : float, # normalised BM25 score (0-1)
        "bm25_fired"        : True,
        "bm25_skipped_gap"  : False,
        "gap_topic"         : None,
        "word_cap_applied"  : bool,
    }

    Result dict schema (when topic gate fires or retriever unavailable)
    -------------------------------------------------------------------
    {
        "bm25_fired"        : False,
        "bm25_skipped_gap"  : bool,  # True if topic gate, False if unavailable
        "gap_topic"         : str | None,  # pattern key that matched, or None
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

    def retrieve(self, query: str, question_id: int = None) -> dict:
        """
        Retrieve the single best-matching training example for *query*.

        Parameters
        ----------
        query : str
            The raw question text from the eval bank.  The topic gate is
            applied against this string using GAP_TOPIC_PATTERNS regexes.
        question_id : int, optional
            Eval question ID for log messages only.  Has no effect on gating.

        Returns
        -------
        dict
            Always returns a dict.  Check ``result["bm25_fired"]`` to know
            whether a retrieved example is present.
        """
        qid_label = f"Q{question_id:02d}" if question_id is not None else "Q??"

        # --- Topic gate -------------------------------------------------------
        # Fire when any confirmed corpus-gap pattern matches the query text.
        # Retrieval is skipped entirely for these topics: the KB contains
        # plausible-but-wrong examples that would worsen the model's answer.
        if self.gap_gate:
            for topic, (pattern, _justification) in GAP_TOPIC_PATTERNS.items():
                if pattern.search(query):
                    if self.verbose:
                        print(
                            f"[BM25RAG] {qid_label} TOPIC GATE -- retrieval skipped: "
                            f"'{topic}' matched '{query[:50]}...'"
                        )
                    return {
                        "bm25_fired":       False,
                        "bm25_skipped_gap": True,
                        "gap_topic":        topic,
                    }

        # --- Unavailable guard -----------------------------------------------
        if not self.available:
            return {"bm25_fired": False, "bm25_skipped_gap": False, "gap_topic": None}

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
            print(
                f"[BM25RAG] {qid_label}  score={norm_score:.3f}  "
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
            "gap_topic":        None,
            "word_cap_applied": cap_applied,
        }

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------

    def diagnose(self, query: str, question_id: int = None, top_n: int = 3):
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

        # Check which topic patterns would fire
        gated_topics = [
            topic for topic, (pattern, _) in GAP_TOPIC_PATTERNS.items()
            if pattern.search(query)
        ]
        qid_label = f"Q{question_id:02d}" if question_id is not None else "Q??"
        print(f"\n{'='*60}")
        print(f"  BM25 Diagnose  {qid_label}")
        print(f"  Query  : {query[:70]}")
        if gated_topics:
            print(f"  Gated  : YES -- topic patterns: {', '.join(gated_topics)}")
        else:
            print(f"  Gated  : no")
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
    p.add_argument("--question_id",  type=int, default=None,
                   help="Optional eval question ID (for log labels only; no effect on gating)")
    p.add_argument("--question",     default="",
                   help="Question text to retrieve for.  If omitted, runs smoke-test suite.")
    p.add_argument("--top_n",        type=int, default=3,
                   help="Number of results to show in diagnose mode (default: 3)")
    p.add_argument("--no_gap_gate",  action="store_true",
                   help="Disable the topic gap gate for this diagnostic run")
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
        result = retriever.retrieve(args.question, question_id=args.question_id)
        print(json.dumps(result, indent=2, ensure_ascii=False))

        print("\n--- diagnose() (gap gate bypassed for ranking) ---")
        retriever.diagnose(args.question, question_id=args.question_id, top_n=args.top_n)

    else:
        # Smoke-test: verify topic gate fires on gap topics and passes clean queries.
        # Each tuple: (question_text, expect_gated, description)
        SMOKE_TESTS = [
            # --- Should FIRE (not gap topics) ---
            ("How do you perform CPR on an adult?",
             False, "CPR adult -- should fire"),
            ("What are the signs and treatment for anaphylaxis?",
             False, "Anaphylaxis -- should fire"),
            ("How do you treat a severe burn on the arm after 5 minutes?",
             False, "Burn (no cool keyword) -- should fire"),
            ("Signs and treatment of heat stroke in a runner?",
             False, "Heat stroke -- should fire"),
            ("Glass embedded in thigh -- what do you do?",
             False, "Embedded object -- should fire (no gap in v3)"),

            # --- Should be GATED (topic patterns) ---
            ("How do you apply a tourniquet to control arterial bleeding?",
             True,  "tourniquet_escalation -- should be gated"),
            ("How do you help a choking infant under 1 year?",
             True,  "infant_choking -- should be gated"),
            ("Should a rescuer apply a tourniquet to a snake bite?",
             True,  "tourniquet_escalation (snake context) -- should be gated"),
            ("How do you log-roll a casualty with spinal injury?",
             True,  "spinal_logroll -- should be gated"),
            ("What is the correct way to apply a chest seal?",
             True,  "chest_seal -- should be gated"),
            ("Can you give naloxone for opioid overdose at home?",
             True,  "naloxone_opioid -- should be gated"),
            ("How do you give rescue breaths to a drowning child?",
             True,  "rescue_breaths_drowning -- should be gated"),
            ("How long should you cool a burn under running water?",
             True,  "burn_cooling -- should be gated"),
        ]

        print("\n" + "=" * 70)
        print("  BM25 RAG Smoke Test -- topic gate (v3)")
        print("=" * 70)

        fired  = 0
        gated  = 0
        errors = 0
        for qtext, expect_gated, desc in SMOKE_TESTS:
            result = retriever.retrieve(qtext)
            actual_gated = result["bm25_skipped_gap"]
            actual_fired = result["bm25_fired"]
            ok = (actual_gated == expect_gated)
            if not ok:
                errors += 1

            status = "GATED " if actual_gated else ("FIRED " if actual_fired else "SKIP  ")
            flag   = "OK" if ok else "FAIL"
            topic  = result.get("gap_topic", "") or ""
            score_s = f"score={result['score']:.3f}" if actual_fired else f"topic={topic}"

            print(f"  [{flag}] {status} {score_s:<22}  {desc}")
            if actual_fired:
                fired += 1
            elif actual_gated:
                gated += 1

        print(f"\n  Fired: {fired}  |  Gap-gated: {gated}  |  Errors: {errors}  "
              f"|  Total: {len(SMOKE_TESTS)}")
        if errors == 0:
            print("  ALL ASSERTIONS PASSED")
        else:
            print(f"  WARNING: {errors} assertion(s) failed (expected vs actual gating)")
        print("=" * 70)
        print("\nRun with --question to test a specific query.")
        print("Run with --no_gap_gate to see what BM25 retrieves without gating.")
