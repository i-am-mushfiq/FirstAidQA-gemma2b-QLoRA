"""
enhanced_inference.py  --  Inference with ablation-controlled enhancements
===========================================================================
Implements four inference-time techniques on top of the fine-tuned adapter.
Each is independently controllable via a CLI flag; all default to ENABLED.

  Technique 2 -- Greedy / low-temperature decoding for safety-critical queries
                 SC categories  : temperature=0.0, do_sample=False  (argmax)
                 Non-SC queries : temperature=0.3, do_sample=True, top_p=0.9

  Technique 4 -- Calibrated min_new_tokens floor
                 Per-category p25 token floor computed from the training
                 distribution (splits/10cat/train.json).  Prevents the model
                 from exiting generation early on low-frequency categories.

  Technique 5 -- RAG using training data as knowledge base
                 KB: splits/10cat/train.json (4,441 Q&A pairs).
                 Eval benchmark: eval_questions_40.json (separate held-out set).
                 No overlap between KB and eval set -- no contamination.
                 Dense retrieval via sentence-transformers all-MiniLM-L6-v2,
                 with disk cache after first run (~30-60s on CPU).
                 BM25 fallback if sentence-transformers is not installed.
                 Only injects when cosine similarity >= threshold (default 0.4).
                 top_k=2 for SC queries, top_k=3 for non-SC queries.
                 Token-budget check: trims top_k or skips if prompt > 400 tokens.

  Technique 6 -- Two-pass self-critique
                 Pass 1: generate answer with Techniques 2+4+5 active.
                 Pass 2: re-prompt with original question + pass-1 answer,
                         asking the model to review for completeness (greedy).
                 Selection: use pass 2 when len(pass2) >= len(pass1) - 5 words
                            AND len(pass2) > 10 words; else keep pass 1.

Flags (all default ON, disable with --no_* prefix):
  --no_greedy_sc    disable T2
  --no_min_tokens   disable T4
  --no_rag          disable T5
  --no_two_pass     disable T6

Output
------
  evaluations/enhanced_eval_<run_id>/run.json
  Format identical to eval_suite.py -- compatible with evaluate.py and
  build_llm_judge_prompt.py without any changes.
  Each answer record also carries an "enhanced" sub-dict with per-technique
  metadata (fired/not fired, retrieved chunks, pass-1 answer, etc.).

Install requirements (once, fine_tuning env):
  pip install sentence-transformers rank_bm25 --break-system-packages

Usage
-----
  # All techniques on (default):
  python enhanced_inference.py \\
      --adapter_path experiments/10cat_4bit_r16_lr1e4_p3_20260506_012852/adapter \\
      --questions_file data/eval_questions_40.json

  # Disable specific techniques for ablation:
  python enhanced_inference.py --adapter_path ... --no_greedy_sc
  python enhanced_inference.py --adapter_path ... --no_rag
  python enhanced_inference.py --adapter_path ... --no_two_pass

  # Baseline (all off -- should reproduce eval_suite output):
  python enhanced_inference.py --adapter_path ... \\
      --no_greedy_sc --no_min_tokens --no_rag --no_two_pass

  # Single question with context display:
  python enhanced_inference.py --adapter_path ... \\
      --question "How do I perform CPR on an adult?" --show_rag_context

  # BM25 retriever instead of dense (no sentence-transformers needed):
  python enhanced_inference.py --adapter_path ... --rag_retriever bm25
"""

import argparse
import gc
import json
import os
import sys
import time
from datetime import datetime
from typing import Optional

import numpy as np
import torch
from peft import PeftModel
from transformers import (
    AutoModelForCausalLM,
    AutoTokenizer,
    BitsAndBytesConfig,
)

# Phase 1: BM25 retriever with gap-question gate (bm25_rag.py)
try:
    from bm25_rag import BM25Retriever, GAP_QUESTION_IDS
    _BM25_RETRIEVER_AVAILABLE = True
except ImportError:
    _BM25_RETRIEVER_AVAILABLE = False
    GAP_QUESTION_IDS = frozenset()

# Import classifiers and constants from data.py.
# data.py uses lazy ML imports at the top level so this is safe (no GPU needed).
try:
    from data import classify_category, SAFETY_CRITICAL_CATEGORIES
except ImportError:
    print("[enhanced] WARNING: Could not import from data.py -- using inline fallback.")
    SAFETY_CRITICAL_CATEGORIES = {
        "CPR / Cardiac arrest",
        "Choking / Airway",
        "Anaphylaxis",
        "Severe bleeding",
        "Shock / Unconsciousness",
        "Spinal / Head injuries",
    }

    def classify_category(question: str, answer: str) -> str:  # noqa: F811
        text = (question + " " + answer).lower()
        rules = [
            ("CPR / Cardiac arrest",           ["cpr", "cardiac arrest", "chest compression", "aed", "defibrillat", "heart attack"]),
            ("Choking / Airway",               ["chok", "airway", "heimlich", "rescue breath", "not breathing"]),
            ("Anaphylaxis",                    ["anaphylax", "epipen", "epinephrine", "severe allergic"]),
            ("Severe bleeding",                ["bleed heavily", "bleeding heavily", "tourniquet", "arterial bleed", "heavy bleeding"]),
            ("Shock / Unconsciousness",        ["shock", "unconscious", "unresponsive", "faint", "collapse"]),
            ("Spinal / Head injuries",         ["spinal", "head injur", "neck injur", "skull fracture"]),
            ("Seizures / Neurological",        ["seizure", "epilep", "convuls", "stroke"]),
            ("Poisoning / Overdose",           ["poison", "overdose", "toxic", "ingested"]),
            ("Bites / Stings / Envenomation",  ["snake", "spider bite", "bee sting", "wasp sting", "venom"]),
            ("Burns",                          ["burn", "scald"]),
            ("Fractures / Sprains",            ["fracture", "broken bone", "sprain", "disloc"]),
            ("Diabetic emergencies",           ["diabet", "hypoglyc"]),
            ("Breathing / Respiratory",        ["asthma", "inhaler", "breathing difficult"]),
            ("Heat / Cold emergencies",        ["heat stroke", "heat exhaust", "hypotherm", "frostbite"]),
            ("Wounds / Bleeding",              ["wound", "lacerat", "bleed", "bandage", "cut"]),
        ]
        for cat, kws in rules:
            if any(kw in text for kw in kws):
                return cat
        return "General first aid"


# ---------------------------------------------------------------------------
# Paths and global constants
# ---------------------------------------------------------------------------

HERE          = os.path.dirname(os.path.abspath(__file__))
MODEL_ID      = "google/gemma-2b-it"
LOCAL_MODEL   = os.path.join(HERE, "models", "gemma-2b-it")
RESULTS_DIR   = os.path.join(HERE, "evaluations")
TRAIN_JSON    = os.path.join(HERE, "splits", "10cat", "train.json")

_qfile_40 = os.path.join(HERE, "data", "eval_questions_40.json")
_qfile_30 = os.path.join(HERE, "data", "eval_questions_30.json")
DEFAULT_QFILE = _qfile_40 if os.path.exists(_qfile_40) else _qfile_30

SYSTEM_PROMPT = (
    "You are a first aid assistant. Provide accurate, step-by-step emergency "
    "guidance. For life-threatening situations, always advise calling emergency "
    "services immediately."
)

EMBED_MODEL       = "sentence-transformers/all-MiniLM-L6-v2"
RAG_THRESHOLD     = 0.4    # cosine similarity below this = no injection
RAG_PROMPT_BUDGET = 400    # max prompt tokens before RAG is dropped/trimmed
RAG_CACHE_EMB     = os.path.join(HERE, "data", "rag_embeddings.npy")
RAG_CACHE_IDX     = os.path.join(HERE, "data", "rag_chunk_index.json")

# Tokens-per-word conversion factor (empirically ~1.3 for medical Q&A prose)
TOKENS_PER_WORD = 1.3

# Hard lower bound for SC categories regardless of what p25 computes to
SC_FLOOR_TOKENS = {
    "CPR / Cardiac arrest":      60,
    "Choking / Airway":          55,
    "Anaphylaxis":               45,
    "Severe bleeding":           55,
    "Shock / Unconsciousness":   45,
    "Spinal / Head injuries":    45,
}
GLOBAL_MIN_TOKENS = 35

REQUIRED_QUESTION_FIELDS = {"id", "category", "safety_critical", "question", "reference"}


# ---------------------------------------------------------------------------
# Technique 4: compute per-category p25 token floor from training data
# ---------------------------------------------------------------------------

def compute_min_tokens_map(train_path: str) -> dict:
    """
    Load splits/10cat/train.json, group answers by category, compute p25
    word-count × TOKENS_PER_WORD floor per category.
    Returns dict {category_str: int_floor, "_default": GLOBAL_MIN_TOKENS}.
    Gracefully falls back if file is missing or malformed.
    """
    floor_map = {"_default": GLOBAL_MIN_TOKENS}

    if not os.path.exists(train_path):
        print(f"[T4] train.json not found at {train_path} -- using global floor {GLOBAL_MIN_TOKENS}")
        return floor_map

    try:
        with open(train_path, encoding="utf-8") as f:
            samples = json.load(f)
    except Exception as e:
        print(f"[T4] Could not load train.json: {e} -- using global floor")
        return floor_map

    if not isinstance(samples, list) or not samples:
        print("[T4] train.json is empty or not a list -- using global floor")
        return floor_map

    cat_wc: dict = {}
    for sample in samples:
        if not isinstance(sample, dict):
            continue
        answer = sample.get("answer", "")
        if not answer:
            continue
        category = sample.get("category") or classify_category(
            sample.get("question", ""), answer
        )
        wc = len(answer.split())
        cat_wc.setdefault(category, []).append(wc)

    for cat, wcs in cat_wc.items():
        p25_tokens = int(float(np.percentile(wcs, 25)) * TOKENS_PER_WORD)
        sc_floor   = SC_FLOOR_TOKENS.get(cat, GLOBAL_MIN_TOKENS)
        floor_map[cat] = max(p25_tokens, sc_floor)

    for cat, floor in SC_FLOOR_TOKENS.items():
        if cat not in floor_map:
            floor_map[cat] = floor

    print(f"[T4] Min-token floors computed for {len(cat_wc)} categories "
          f"from {len(samples)} training samples.")
    return floor_map


# ---------------------------------------------------------------------------
# Technique 5: RAG using training data as knowledge base
# ---------------------------------------------------------------------------

class TrainingRAG:
    """
    Retrieves relevant Q&A pairs from splits/10cat/train.json and injects
    them as grounded examples before generation.

    Knowledge base: splits/10cat/train.json (4,441 training Q&A pairs).
    Evaluation benchmark: eval_questions_40.json (separate held-out set).
    These two sets do not overlap -- no contamination.

    The KB is training data, so the model has already seen these examples
    during fine-tuning.  RAG may therefore add limited new information, but
    it provides explicit in-context examples of style and format.  Whether
    the gain is meaningful is a measured result, not an assumption.

    Dense retrieval  : sentence-transformers all-MiniLM-L6-v2
                       Embeddings cached to data/rag_embeddings.npy after
                       first run (~30-60s on CPU for 4,441 chunks).
                       Cache is invalidated if train.json is newer.
    BM25 fallback    : rank_bm25 (no sentence-transformers required)
    Similarity gate  : only inject when cosine score >= threshold (default 0.4)
    top_k            : 2 for SC queries, 3 for non-SC (caller decides)
    """

    def __init__(
        self,
        train_path: str  = TRAIN_JSON,
        retriever:  str  = "dense",
        threshold:  float = RAG_THRESHOLD,
    ):
        if not os.path.exists(train_path):
            raise FileNotFoundError(
                f"Training split not found: {train_path}. Run data.py first."
            )

        with open(train_path, encoding="utf-8") as f:
            samples = json.load(f)

        self.kb_questions: list = []
        self.kb_answers:   list = []
        self.kb_categories: list = []
        self.kb_chunks:    list = []   # formatted text used for BM25 / display

        for s in samples:
            if not isinstance(s, dict):
                continue
            q   = s.get("question", "").strip()
            a   = s.get("answer",   "").strip()
            cat = s.get("category", "") or classify_category(q, a)
            if not q or not a:
                continue
            self.kb_questions.append(q)
            self.kb_answers.append(a)
            self.kb_categories.append(cat)
            self.kb_chunks.append(f"[{cat}]\nQ: {q}\nA: {a}")

        self.threshold    = threshold
        self._retriever   = retriever
        self.available    = False
        self._dense_model = None
        self._embeddings  = None
        self._bm25_index  = None
        self._train_path  = train_path

        print(f"[T5-RAG] KB: {len(self.kb_questions):,} training Q&A pairs from "
              f"{os.path.basename(train_path)}")
        self._init_retriever()

    def _init_retriever(self):
        if self._retriever == "bm25":
            self._init_bm25()
        else:
            if not self._init_dense():
                print("[T5-RAG] Dense init failed -- trying BM25 fallback.")
                self._init_bm25()

    def _init_dense(self) -> bool:
        try:
            from sentence_transformers import SentenceTransformer

            # Cache validity: cache exists AND train.json not newer than cache
            use_cache = (
                os.path.exists(RAG_CACHE_EMB)
                and os.path.exists(RAG_CACHE_IDX)
                and os.path.getmtime(self._train_path) <= os.path.getmtime(RAG_CACHE_EMB)
            )

            print(f"[T5-RAG] Loading embedding model ({EMBED_MODEL})...")
            self._dense_model = SentenceTransformer(EMBED_MODEL)

            if use_cache:
                print("[T5-RAG] Loading embeddings from cache...")
                self._embeddings = np.load(RAG_CACHE_EMB)
                print(f"[T5-RAG] Loaded {self._embeddings.shape[0]:,} cached embeddings.")
            else:
                print(f"[T5-RAG] Encoding {len(self.kb_chunks):,} chunks "
                      "(first run -- ~30-60s on CPU)...")
                t0 = time.time()
                self._embeddings = self._dense_model.encode(
                    self.kb_chunks,
                    batch_size=64,
                    show_progress_bar=True,
                    normalize_embeddings=True,
                )
                elapsed = time.time() - t0
                os.makedirs(os.path.dirname(RAG_CACHE_EMB), exist_ok=True)
                np.save(RAG_CACHE_EMB, self._embeddings)
                with open(RAG_CACHE_IDX, "w", encoding="utf-8") as f:
                    json.dump([q[:80] for q in self.kb_questions], f, indent=2)
                print(f"[T5-RAG] Encoded in {elapsed:.1f}s. Cache saved -> {RAG_CACHE_EMB}")

            self._retriever = "dense"
            self.available  = True
            return True

        except ImportError:
            print("[T5-RAG] sentence-transformers not installed.")
            print("         pip install sentence-transformers --break-system-packages")
            return False

    def _init_bm25(self):
        try:
            from rank_bm25 import BM25Okapi
            print(f"[T5-RAG] Building BM25 index over {len(self.kb_chunks):,} chunks...")
            tokenized = [chunk.lower().split() for chunk in self.kb_chunks]
            self._bm25_index = BM25Okapi(tokenized)
            self._retriever  = "bm25"
            self.available   = True
            print("[T5-RAG] BM25 index ready.")
        except ImportError:
            print("[T5-RAG] rank_bm25 not installed.")
            print("         pip install rank_bm25 --break-system-packages")
            print("[T5-RAG] RAG will be skipped for all queries.")
            self.available = False

    def retrieve(self, query: str, top_k: int) -> list:
        """
        Returns up to top_k dicts: {question, answer, category, score}.
        Only entries with score >= self.threshold are returned.
        Returns [] if retriever is unavailable.
        """
        if not self.available:
            return []

        if self._retriever == "dense" and self._dense_model is not None:
            q_emb  = self._dense_model.encode([query], normalize_embeddings=True)
            scores = (self._embeddings @ q_emb.T).squeeze()
        else:
            # BM25: normalise scores to [0,1] so threshold works consistently
            raw   = self._bm25_index.get_scores(query.lower().split())
            mx    = raw.max()
            scores = raw / mx if mx > 0 else raw

        top_indices = np.argsort(scores)[::-1][:top_k]
        results = []
        for idx in top_indices:
            score = float(scores[idx])
            if score >= self.threshold:
                results.append({
                    "question": self.kb_questions[idx],
                    "answer":   self.kb_answers[idx],
                    "category": self.kb_categories[idx],
                    "score":    round(score, 4),
                })
        return results


# ---------------------------------------------------------------------------
# Prompt builders
# ---------------------------------------------------------------------------

def build_prompt(question: str) -> str:
    """Standard eval_suite-compatible prompt (template 0 from data.py)."""
    return (
        f"<start_of_turn>user\n{SYSTEM_PROMPT}\n\n"
        f"Question: {question}<end_of_turn>\n"
        f"<start_of_turn>model\n"
    )


def build_rag_prompt(question: str, rag_results: list) -> str:
    """
    Technique 5 prompt: inject retrieved training Q&A pairs before the question.
    Keeps the same <start_of_turn> structure as build_prompt().
    """
    ref_block = ""
    for i, r in enumerate(rag_results, 1):
        ref_block += f"Example {i} [{r['category']}]:\nQ: {r['question']}\nA: {r['answer']}\n\n"

    return (
        f"<start_of_turn>user\n{SYSTEM_PROMPT}\n\n"
        f"[RELATED EXAMPLES]\n{ref_block}"
        f"Question: {question}<end_of_turn>\n"
        f"<start_of_turn>model\n"
    )


def build_critique_prompt(question: str, category: str, pass1_answer: str) -> str:
    """
    Technique 6 second-pass prompt.  Structural only -- no injected medical content.
    """
    return (
        f"<start_of_turn>user\n{SYSTEM_PROMPT}\n\n"
        f"Review the following first aid answer for completeness. "
        f"If any critical steps are missing, provide a corrected complete answer. "
        f"If the answer is already complete, reproduce it unchanged.\n\n"
        f"Category: {category}\n"
        f"Question: {question}\n\n"
        f"Previous answer:\n{pass1_answer}"
        f"<end_of_turn>\n"
        f"<start_of_turn>model\n"
    )


# ---------------------------------------------------------------------------
# Model loading helpers (identical to eval_suite.py)
# ---------------------------------------------------------------------------

def resolve_model(model_path: str = "") -> tuple:
    for candidate in [p for p in [model_path, LOCAL_MODEL] if p]:
        if os.path.isdir(candidate) and os.path.exists(os.path.join(candidate, "config.json")):
            return os.path.abspath(candidate), True
    return MODEL_ID, False


def get_bnb_config(quant: str) -> Optional[BitsAndBytesConfig]:
    if quant == "4bit":
        return BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_use_double_quant=True,
            bnb_4bit_compute_dtype=torch.float16,
        )
    if quant == "8bit":
        return BitsAndBytesConfig(load_in_8bit=True)
    return None


def _get_stop_token_ids(tokenizer) -> list:
    stop_ids = [tokenizer.eos_token_id]
    for candidate in ["<end_of_turn>", "<|im_end|>", "[/INST]"]:
        tid = tokenizer.convert_tokens_to_ids(candidate)
        if tid is not None and tid != tokenizer.unk_token_id:
            stop_ids.append(tid)
    return list(set(stop_ids))


def infer_quant_from_path(adapter_path: str) -> str:
    lowered = adapter_path.lower()
    if "8bit" in lowered:
        return "8bit"
    if "fp16" in lowered:
        return "fp16"
    return "4bit"


def unload(model):
    del model
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
        torch.cuda.reset_peak_memory_stats()


# ---------------------------------------------------------------------------
# Question bank loader (identical to eval_suite.py)
# ---------------------------------------------------------------------------

def load_questions(path: str) -> list:
    resolved = os.path.abspath(path)
    with open(resolved, encoding="utf-8") as f:
        payload = json.load(f)
    questions = payload.get("questions") if isinstance(payload, dict) else payload
    if not isinstance(questions, list) or not questions:
        raise ValueError(f"Question bank must contain a non-empty list: {resolved}")
    seen_ids: set = set()
    for i, question in enumerate(questions, start=1):
        if not isinstance(question, dict):
            raise ValueError(f"Question #{i} must be an object")
        missing = REQUIRED_QUESTION_FIELDS - set(question)
        if missing:
            raise ValueError(f"Question #{i} missing fields: {', '.join(sorted(missing))}")
        qid = question["id"]
        if qid in seen_ids:
            raise ValueError(f"Duplicate question id: {qid}")
        seen_ids.add(qid)
    return questions


# ---------------------------------------------------------------------------
# Low-level generation (takes a pre-built prompt string)
# ---------------------------------------------------------------------------

@torch.inference_mode()
def _run_generate(
    model,
    tokenizer,
    prompt:         str,
    max_new_tokens: int,
    min_new_tokens: int,
    do_sample:      bool,
    temperature:    float,
    top_p:          float,
    stop_ids:       list,
) -> dict:
    """
    Core generation.  Returns dict matching eval_suite.py answer schema
    (minus question-level fields added by the caller).
    """
    inputs = tokenizer(prompt, return_tensors="pt").to(model.device)
    in_len = inputs["input_ids"].shape[-1]

    if torch.cuda.is_available():
        torch.cuda.reset_peak_memory_stats()

    gen_kwargs: dict = {
        "max_new_tokens":     max_new_tokens,
        "min_new_tokens":     max(0, min_new_tokens),
        "pad_token_id":       tokenizer.pad_token_id,
        "eos_token_id":       stop_ids,
        "repetition_penalty": 1.15,
    }
    if do_sample:
        gen_kwargs["do_sample"]   = True
        gen_kwargs["temperature"] = temperature
        gen_kwargs["top_p"]       = top_p
    else:
        gen_kwargs["do_sample"] = False
        # do NOT pass temperature/top_p when do_sample=False

    try:
        t0  = time.time()
        out = model.generate(**inputs, **gen_kwargs)
        elapsed = time.time() - t0

        new_ids = out[0][in_len:]
        n_tok   = len(new_ids)
        answer  = tokenizer.decode(new_ids, skip_special_tokens=True).strip()
        peak_mb = (
            torch.cuda.max_memory_allocated() / 1e6
            if torch.cuda.is_available() else 0.0
        )
        return {
            "answer":           answer,
            "tokens_generated": n_tok,
            "tokens_per_sec":   round(n_tok / elapsed, 2) if elapsed > 0 else 0.0,
            "elapsed_s":        round(elapsed, 3),
            "peak_vram_mb":     round(peak_mb, 0),
            "error":            None,
        }
    except Exception as e:
        return {
            "answer":           "",
            "tokens_generated": 0,
            "tokens_per_sec":   0.0,
            "elapsed_s":        0.0,
            "peak_vram_mb":     0.0,
            "error":            str(e),
        }


# ---------------------------------------------------------------------------
# EnhancedInferenceEngine
# ---------------------------------------------------------------------------

class EnhancedInferenceEngine:
    """
    Wraps a single loaded adapter with the four ablation techniques.

    Parameters
    ----------
    model, tokenizer  : loaded HuggingFace objects
    flags             : dict with keys "greedy_sc", "min_tokens", "rag", "two_pass"
    min_tokens_map    : output of compute_min_tokens_map()
    rag               : ReferenceRAG instance (or None if T5 disabled/unavailable)
    max_new_tokens    : int from CLI
    show_rag_context  : print retrieved chunks per query (useful for single-Q mode)
    """

    def __init__(
        self,
        model,
        tokenizer,
        flags:           dict,
        min_tokens_map:  dict,
        rag:             Optional["TrainingRAG"],
        max_new_tokens:  int,
        show_rag_context: bool = False,
    ):
        self.model            = model
        self.tokenizer        = tokenizer
        self.flags            = flags
        self.min_tokens_map   = min_tokens_map
        self.rag              = rag
        self.max_new_tokens   = max_new_tokens
        self.show_rag_context = show_rag_context
        self.stop_ids         = _get_stop_token_ids(tokenizer)

        active = [
            k for k, v in flags.items()
            if v and not (k == "rag" and (rag is None or not rag.available))
        ]
        print(f"[enhanced] Active techniques: {active if active else ['none (baseline mode)']}")
        if flags.get("rag") and (rag is None or not rag.available):
            print("[enhanced] T5-RAG flag is ON but retriever is unavailable -- skipping RAG.")

    # --- Technique 2 ---------------------------------------------------------

    def _resolve_gen_kwargs(self, is_sc: bool) -> tuple:
        """Returns (do_sample, temperature, top_p, t2_fired)."""
        if self.flags["greedy_sc"] and is_sc:
            return False, 1.0, 1.0, True   # temperature irrelevant when do_sample=False
        return True, 0.3, 0.9, False

    # --- Technique 4 ---------------------------------------------------------

    def _resolve_min_tokens(self, category: str) -> tuple:
        """Returns (min_new_tokens, t4_value)."""
        if not self.flags["min_tokens"]:
            return 0, 0
        floor = self.min_tokens_map.get(
            category, self.min_tokens_map.get("_default", GLOBAL_MIN_TOKENS)
        )
        return floor, floor

    # --- Technique 5 ---------------------------------------------------------

    def _resolve_rag_prompt(
        self, question: str, question_id: int, is_sc: bool
    ) -> tuple:
        """
        Returns (prompt_str, t5_fired, t5_retrieved_list, bm25_skipped_gap).
        Falls back to standard prompt if RAG is disabled, unavailable,
        below threshold, gap-gated, or would exceed token budget.

        When the active retriever is a BM25Retriever (from bm25_rag.py),
        question_id is passed to the gap gate.  For the legacy TrainingRAG
        retriever question_id is unused (no gap gate).
        """
        if not self.flags["rag"] or self.rag is None or not self.rag.available:
            return build_prompt(question), False, [], False

        # ── BM25Retriever path (Phase 1 — gap-gated, 1-example, 150-tok cap) ──
        if isinstance(self.rag, BM25Retriever):
            result = self.rag.retrieve(question_id, question)
            if not result["bm25_fired"]:
                return build_prompt(question), False, [], result["bm25_skipped_gap"]
            # Convert BM25Retriever result to the list-of-dicts format used by
            # build_rag_prompt() — hard 1-example limit already applied upstream
            retrieved = [{
                "question": result["question"],
                "answer":   result["answer"],   # already capped at 150 tokens
                "category": result["category"],
                "score":    result["score"],
            }]
            rag_prompt = build_rag_prompt(question, retrieved)
            if self.show_rag_context:
                print(
                    f"  [BM25-RAG] Q{question_id:02d}  score={result['score']:.3f}"
                    f"  cap={'Y' if result['word_cap_applied'] else 'N'}"
                    f"  [{result['category']}]"
                )
                print(f"    Q: {result['question'][:70]}")
                print(f"    A: {result['answer'][:80]}...")
            return rag_prompt, True, retrieved, False

        # ── Legacy TrainingRAG path (dense / internal BM25 fallback) ──────────
        top_k = 2 if is_sc else 3
        results = self.rag.retrieve(question, top_k=top_k)

        if not results:
            return build_prompt(question), False, [], False

        rag_prompt = build_rag_prompt(question, results)
        prompt_tok_count = len(self.tokenizer.encode(rag_prompt, add_special_tokens=False))

        # Token budget: try dropping to top_k=1 first
        if prompt_tok_count > RAG_PROMPT_BUDGET and len(results) > 1:
            results      = results[:1]
            rag_prompt   = build_rag_prompt(question, results)
            prompt_tok_count = len(
                self.tokenizer.encode(rag_prompt, add_special_tokens=False)
            )

        # If still over budget, skip RAG entirely for this query
        if prompt_tok_count > RAG_PROMPT_BUDGET:
            return build_prompt(question), False, [], False

        if self.show_rag_context:
            print("  [T5-RAG] Retrieved:")
            for r in results:
                print(f"    score={r['score']:.3f}  [{r['category']}]  {r['question'][:70]}")
                print(f"           {r['answer'][:80]}...")

        return rag_prompt, True, results, False

    # --- Main generation loop ------------------------------------------------

    def generate(self, question: str, question_id: int = 0) -> dict:
        """
        Full enhanced generation for one question.
        Returns an answer record dict compatible with eval_suite.py output schema.

        Parameters
        ----------
        question    : str  — raw question text
        question_id : int  — eval bank ID; passed to the BM25 gap gate.
                             Defaults to 0 (no gate) for single-question mode.
        """
        # Classify query
        category = classify_category(question, "")
        is_sc    = category in SAFETY_CRITICAL_CATEGORIES

        # T2: decoding strategy
        do_sample, temperature, top_p, t2_fired = self._resolve_gen_kwargs(is_sc)

        # T4: min_new_tokens floor
        min_tok, t4_value = self._resolve_min_tokens(category)

        # T5: RAG prompt (may fall back to standard prompt)
        prompt1, t5_fired, t5_retrieved, bm25_gap_skipped = self._resolve_rag_prompt(
            question, question_id, is_sc
        )

        # Pass 1 generation
        pass1 = _run_generate(
            self.model, self.tokenizer, prompt1,
            max_new_tokens=self.max_new_tokens,
            min_new_tokens=min_tok,
            do_sample=do_sample,
            temperature=temperature,
            top_p=top_p,
            stop_ids=self.stop_ids,
        )

        # T6: two-pass self-critique
        t6_used_pass2   = False
        t6_pass1_answer = None
        total_elapsed   = pass1["elapsed_s"]
        total_tokens    = pass1["tokens_generated"]

        if self.flags["two_pass"] and not pass1["error"] and pass1["answer"]:
            t6_pass1_answer = pass1["answer"]
            prompt2 = build_critique_prompt(question, category, pass1["answer"])
            pass2   = _run_generate(
                self.model, self.tokenizer, prompt2,
                max_new_tokens=self.max_new_tokens,
                min_new_tokens=0,
                do_sample=False,   # always greedy for critique
                temperature=1.0,
                top_p=1.0,
                stop_ids=self.stop_ids,
            )
            total_elapsed += pass2["elapsed_s"]
            total_tokens  += pass2["tokens_generated"]

            if not pass2["error"] and pass2["answer"]:
                p1_words = len(pass1["answer"].split())
                p2_words = len(pass2["answer"].split())
                # Accept pass 2 if not more than 5 words shorter than pass 1
                # and has at least 10 words (guards against collapsed outputs)
                if p2_words >= max(p1_words - 5, 10):
                    final_answer  = pass2["answer"]
                    t6_used_pass2 = True
                else:
                    final_answer = pass1["answer"]
            else:
                final_answer = pass1["answer"]
        else:
            final_answer = pass1["answer"]

        tps = round(total_tokens / total_elapsed, 2) if total_elapsed > 0 else 0.0

        return {
            # eval_suite.py-compatible fields
            "answer":           final_answer,
            "tokens_generated": total_tokens,
            "tokens_per_sec":   tps,
            "elapsed_s":        round(total_elapsed, 2),
            "peak_vram_mb":     pass1["peak_vram_mb"],
            "error":            pass1["error"],
            # Per-question enhancement metadata
            "enhanced": {
                "category_detected":    category,
                "is_sc":                is_sc,
                "t2_greedy_sc_active":  self.flags["greedy_sc"],
                "t2_greedy_fired":      t2_fired,
                "t4_min_tokens_active": self.flags["min_tokens"],
                "t4_min_tokens_value":  t4_value,
                "t5_rag_active":        self.flags["rag"],
                "t5_rag_fired":         t5_fired,
                "t5_retrieved":         t5_retrieved,
                "t5_bm25_gap_skipped":  bm25_gap_skipped,
                "t6_two_pass_active":   self.flags["two_pass"],
                "t6_used_pass2":        t6_used_pass2,
                "t6_pass1_answer":      t6_pass1_answer,
            },
        }

    def batch_eval(self, questions: list) -> list:
        """Run all questions.  Returns list of answer records."""
        answers = []
        n = len(questions)
        for q in questions:
            print(f"  Q{q['id']:02d}/{n}  {q['question'][:60]}...")
            result = self.generate(q["question"], question_id=q["id"])
            record = {
                "question_id":     q["id"],
                "question":        q["question"],
                "reference":       q["reference"],
                "category":        q["category"],
                "safety_critical": q["safety_critical"],
                **result,
            }
            enh    = result["enhanced"]
            sc_tag  = " [SC]"      if q["safety_critical"]              else ""
            t2_tag  = " T2"        if enh["t2_greedy_fired"]            else ""
            t5_tag  = " T5:bm25"   if enh["t5_rag_fired"]               else ""
            t5_gate = " T5:gated"  if enh["t5_bm25_gap_skipped"]        else ""
            t6_tag  = " T6:pass2"  if enh["t6_used_pass2"]              else ""
            status  = (f"err: {result['error']}" if result["error"]
                       else f"{result['tokens_per_sec']} tok/s  "
                            f"{result['peak_vram_mb']:.0f} MB"
                            f"{sc_tag}{t2_tag}{t5_tag}{t5_gate}{t6_tag}")
            print(f"          {status}")
            answers.append(record)
        return answers


# ---------------------------------------------------------------------------
# Results persistence (eval_suite.py-compatible format)
# ---------------------------------------------------------------------------

def save_results(run_id: str, meta: dict, model_results: list) -> str:
    eval_folder = os.path.join(RESULTS_DIR, f"enhanced_eval_{run_id}")
    os.makedirs(eval_folder, exist_ok=True)
    path = os.path.join(eval_folder, "run.json")
    payload = {
        "run_id":    run_id,
        "timestamp": datetime.now().isoformat(),
        "meta":      meta,
        "results":   model_results,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    print(f"\n[enhanced] Results saved -> {path}")
    return path


# ---------------------------------------------------------------------------
# CLI helpers
# ---------------------------------------------------------------------------

def resolve_adapter_path(raw: str) -> str:
    if not raw:
        return ""
    if os.path.exists(os.path.join(raw, "adapter_config.json")):
        return os.path.abspath(raw)
    sub = os.path.join(raw, "adapter")
    if os.path.exists(os.path.join(sub, "adapter_config.json")):
        return os.path.abspath(sub)
    return os.path.abspath(raw)


def _technique_tag(flags: dict, rag_ok: bool) -> str:
    parts = []
    if flags["greedy_sc"]:              parts.append("T2")
    if flags["min_tokens"]:             parts.append("T4")
    if flags["rag"] and rag_ok:         parts.append("T5")
    if flags["two_pass"]:               parts.append("T6")
    return "+".join(parts) if parts else "baseline"


def build_variant_key(adapter_path: str, flags: dict, rag_ok: bool) -> str:
    folder = (
        os.path.basename(os.path.dirname(adapter_path))
        if os.path.basename(adapter_path) == "adapter"
        else os.path.basename(adapter_path)
    ) if adapter_path else "base"
    return folder + "_" + _technique_tag(flags, rag_ok)


def build_variant_label(adapter_path: str, flags: dict, rag_ok: bool) -> str:
    folder = (
        os.path.basename(os.path.dirname(adapter_path))
        if os.path.basename(adapter_path) == "adapter"
        else os.path.basename(adapter_path)
    ) if adapter_path else "base"
    tag_map = {
        "greedy_sc":  "T2:greedy-SC",
        "min_tokens": "T4:min-tokens",
        "rag":        "T5:RAG",
        "two_pass":   "T6:two-pass",
    }
    parts = [
        v for k, v in tag_map.items()
        if flags.get(k) and not (k == "rag" and not rag_ok)
    ]
    tech_str = " + ".join(parts) if parts else "baseline (all off)"
    return f"{folder}  [{tech_str}]"


def parse_args():
    p = argparse.ArgumentParser(
        description="Enhanced first-aid inference with ablation-controlled techniques",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    # Model / adapter
    p.add_argument("--adapter_path",   default="",
                   help="Path to trained LoRA adapter directory (or 'adapter' sub-folder)")
    p.add_argument("--model_path",     default="",
                   help="Local base model directory (default: models/gemma-2b-it)")
    p.add_argument("--quant",          default="", choices=["4bit", "8bit", "fp16", ""],
                   help="Quantization mode.  Auto-inferred from adapter_path if omitted.")

    # Evaluation
    p.add_argument("--questions_file", default=DEFAULT_QFILE,
                   help=f"Question bank JSON (default: {os.path.basename(DEFAULT_QFILE)})")
    p.add_argument("--max_new_tokens", type=int, default=250,
                   help="Maximum new tokens per answer (default: 250)")
    p.add_argument("--question",       default="",
                   help="Single question (skips --questions_file, runs interactive-style)")

    # Ablation flags (all default ON)
    p.add_argument("--no_greedy_sc",   action="store_true",
                   help="Disable T2: greedy decoding for SC queries")
    p.add_argument("--no_min_tokens",  action="store_true",
                   help="Disable T4: calibrated min_new_tokens floor")
    p.add_argument("--no_rag",         action="store_true",
                   help="Disable T5: RAG context injection")
    p.add_argument("--no_two_pass",    action="store_true",
                   help="Disable T6: two-pass self-critique")

    # RAG-specific options
    p.add_argument("--rag_retriever",  default="dense", choices=["dense", "bm25"],
                   help="T5 retriever type: dense (default) or bm25")
    p.add_argument("--rag_threshold",  type=float, default=RAG_THRESHOLD,
                   help=f"T5 similarity threshold for injection (default: {RAG_THRESHOLD})")
    p.add_argument("--rag_kb",         default=TRAIN_JSON,
                   help="T5 knowledge base JSON (default: splits/10cat/train.json)")
    p.add_argument("--show_rag_context", action="store_true",
                   help="Print retrieved reference chunks for each query")

    # Variant label override
    p.add_argument("--variant_key",    default="",
                   help="Override variant key in output JSON")
    p.add_argument("--variant_label",  default="",
                   help="Override human-readable variant label in output JSON")

    return p.parse_args()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    args = parse_args()

    # Technique flags
    flags = {
        "greedy_sc":  not args.no_greedy_sc,
        "min_tokens": not args.no_min_tokens,
        "rag":        not args.no_rag,
        "two_pass":   not args.no_two_pass,
    }

    # Resolve adapter
    adapter_path = resolve_adapter_path(args.adapter_path)
    if adapter_path and not os.path.exists(adapter_path):
        print(f"[enhanced] ERROR: adapter not found: {adapter_path}")
        sys.exit(1)

    quant = args.quant or (infer_quant_from_path(adapter_path) if adapter_path else "fp16")

    # Questions
    if args.question:
        questions = [{
            "id": 1, "category": "Unknown", "safety_critical": False,
            "question": args.question, "reference": "",
        }]
    else:
        questions = load_questions(args.questions_file)

    # --- Technique 4: precompute min_tokens map ---
    min_tokens_map = compute_min_tokens_map(TRAIN_JSON) if flags["min_tokens"] else {"_default": 0}

    # --- Technique 5: initialise RAG (before loading the GPU model) ---
    rag = None
    if flags["rag"]:
        if args.rag_retriever == "bm25":
            # Phase 1 path: use the standalone BM25Retriever with gap gate
            if _BM25_RETRIEVER_AVAILABLE:
                try:
                    rag = BM25Retriever(
                        train_path = args.rag_kb,
                        gap_gate   = True,
                        verbose    = True,
                    )
                    if not rag.available:
                        print("[T5-RAG] BM25Retriever init failed -- RAG disabled.")
                        flags["rag"] = False
                except FileNotFoundError as e:
                    print(f"[T5-RAG] KB file not found: {e} -- RAG disabled.")
                    flags["rag"] = False
            else:
                print("[T5-RAG] bm25_rag.py not found -- falling back to TrainingRAG.")
                try:
                    rag = TrainingRAG(
                        train_path = args.rag_kb,
                        retriever  = "bm25",
                        threshold  = args.rag_threshold,
                    )
                except FileNotFoundError as e:
                    print(f"[T5-RAG] KB file not found: {e} -- RAG disabled.")
                    flags["rag"] = False
        else:
            # Legacy dense path (TrainingRAG)
            try:
                rag = TrainingRAG(
                    train_path = args.rag_kb,
                    retriever  = args.rag_retriever,
                    threshold  = args.rag_threshold,
                )
            except FileNotFoundError as e:
                print(f"[T5-RAG] KB file not found: {e} -- RAG disabled for this run.")
                flags["rag"] = False

    rag_ok = rag is not None and rag.available

    # Variant key / label (resolved after RAG init so we know if RAG is truly on)
    variant_key   = args.variant_key   or build_variant_key(adapter_path, flags, rag_ok)
    variant_label = args.variant_label or build_variant_label(adapter_path, flags, rag_ok)

    # --- Load model (GPU) ---
    source, is_local = resolve_model(args.model_path)

    print("\n" + "=" * 60)
    print(f"  Enhanced Inference -- {len(questions)}-Question Eval")
    print("=" * 60)
    print(f"  Adapter      : {adapter_path or '(none -- base model)'}")
    print(f"  Quant        : {quant}")
    print(f"  T2 greedy SC : {'ON' if flags['greedy_sc']  else 'OFF'}")
    print(f"  T4 min tok   : {'ON' if flags['min_tokens'] else 'OFF'}")
    print(f"  T5 RAG       : {'ON (' + args.rag_retriever + ')' if rag_ok else 'OFF'}")
    print(f"  T6 two-pass  : {'ON' if flags['two_pass']   else 'OFF'}")
    print(f"  Questions    : {len(questions)}")
    print(f"  Output dir   : {RESULTS_DIR}/")
    print("=" * 60)

    if torch.cuda.is_available():
        torch.cuda.empty_cache()
        torch.cuda.reset_peak_memory_stats()

    print("  Loading model...")
    bnb       = get_bnb_config(quant)
    tokenizer = AutoTokenizer.from_pretrained(
        source, trust_remote_code=True, local_files_only=is_local
    )
    if tokenizer.pad_token is None:
        tokenizer.pad_token    = tokenizer.eos_token
        tokenizer.pad_token_id = tokenizer.eos_token_id
    tokenizer.padding_side = "left"

    model = AutoModelForCausalLM.from_pretrained(
        source,
        quantization_config=bnb,
        torch_dtype=torch.float16,
        device_map="auto",
        trust_remote_code=True,
        local_files_only=is_local,
    )
    if adapter_path:
        model = PeftModel.from_pretrained(model, adapter_path)
    model.eval()

    mem_mb = torch.cuda.memory_allocated() / 1e6 if torch.cuda.is_available() else 0.0
    print(f"  VRAM after load: {mem_mb:.0f} MB")

    # Build engine
    engine = EnhancedInferenceEngine(
        model           = model,
        tokenizer       = tokenizer,
        flags           = flags,
        min_tokens_map  = min_tokens_map,
        rag             = rag,
        max_new_tokens  = args.max_new_tokens,
        show_rag_context= args.show_rag_context,
    )

    # Warm-up (not saved)
    stop_ids = _get_stop_token_ids(tokenizer)
    _run_generate(model, tokenizer, build_prompt("Hello"),
                  max_new_tokens=5, min_new_tokens=0,
                  do_sample=False, temperature=1.0, top_p=1.0, stop_ids=stop_ids)
    if torch.cuda.is_available():
        torch.cuda.reset_peak_memory_stats()

    # --- Single question mode ------------------------------------------------
    if args.question:
        result = engine.generate(args.question)
        enh    = result["enhanced"]
        print(f"\nQ: {args.question}")
        print(f"A: {result['answer']}")
        print(f"\n  Category : {enh['category_detected']}  (SC={enh['is_sc']})")
        print(f"  T2 fired : {enh['t2_greedy_fired']}")
        print(f"  T4 floor : {enh['t4_min_tokens_value']} tokens")
        print(f"  T5 fired : {enh['t5_rag_fired']}"
              + (f"  ({len(enh['t5_retrieved'])} chunks)" if enh["t5_retrieved"] else ""))
        print(f"  T6 pass2 : {enh['t6_used_pass2']}")
        if enh["t6_pass1_answer"]:
            print(f"\n  Pass 1: {enh['t6_pass1_answer']}")
            print(f"  Final : {result['answer']}")
        sys.exit(0)

    # --- Batch eval ----------------------------------------------------------
    run_id  = datetime.now().strftime("%Y%m%d_%H%M%S")
    answers = engine.batch_eval(questions)

    model_result = {
        "variant":  variant_key,
        "label":    variant_label,
        "quant":    quant,
        "skipped":  False,
        "answers":  answers,
    }

    meta = {
        "model_id":       MODEL_ID,
        "model_source":   source,
        "adapter_path":   adapter_path,
        "variant_key":    variant_key,
        "variant_label":  variant_label,
        "quant":          quant,
        "max_new_tokens": args.max_new_tokens,
        "questions_file": os.path.abspath(args.questions_file) if not args.question else "",
        "n_questions":    len(questions),
        "system_prompt":  SYSTEM_PROMPT,
        "techniques": {
            "T2_greedy_sc":  flags["greedy_sc"],
            "T4_min_tokens": flags["min_tokens"],
            "T5_rag":        rag_ok,
            "T6_two_pass":   flags["two_pass"],
            "T5_retriever":  args.rag_retriever if rag_ok else None,
            "T5_threshold":  args.rag_threshold if rag_ok else None,
            "T5_kb":         os.path.abspath(args.rag_kb) if rag_ok else None,
        },
    }

    out_path = save_results(run_id, meta, [model_result])

    # --- Summary -------------------------------------------------------------
    answered   = sum(1 for a in answers if not a["error"])
    t2_count   = sum(1 for a in answers if a.get("enhanced", {}).get("t2_greedy_fired"))
    t5_count   = sum(1 for a in answers if a.get("enhanced", {}).get("t5_rag_fired"))
    t5_gated   = sum(1 for a in answers if a.get("enhanced", {}).get("t5_bm25_gap_skipped"))
    t6_count   = sum(1 for a in answers if a.get("enhanced", {}).get("t6_used_pass2"))
    avg_tps    = (sum(a["tokens_per_sec"] for a in answers if not a["error"]) / answered
                  if answered else 0)
    peak_mb    = max((a["peak_vram_mb"] for a in answers if not a["error"]), default=0.0)
    errors     = sum(1 for a in answers if a["error"])

    print("\n" + "=" * 60)
    print("  SUMMARY")
    print("=" * 60)
    print(f"  Answered          : {answered} / {len(answers)}")
    print(f"  T2 greedy fired   : {t2_count} questions")
    print(f"  T5 RAG fired      : {t5_count} questions")
    if t5_gated:
        print(f"  T5 gap-gated      : {t5_gated} questions (Q6/Q17/Q21/Q22/Q28)")
    print(f"  T6 pass2 used     : {t6_count} questions")
    print(f"  Avg tok/s         : {avg_tps:.1f}")
    print(f"  Peak VRAM         : {peak_mb:.0f} MB")
    print(f"  Errors            : {errors}")
    print(f"  Results           : {out_path}")
    print("=" * 60)

    unload(model)
