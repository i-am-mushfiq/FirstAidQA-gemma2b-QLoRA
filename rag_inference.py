"""
rag_inference.py  --  RAG-augmented inference for first-aid LoRA adapters
=========================================================================
Retrieves the most relevant training Q&A pairs at query time and injects
them as grounded context before the model generates its answer.

Architecture
------------
  Knowledge base : splits/10cat/train.json  (4,441 Q&A pairs, all categories)
  Retriever      : Dense  -- sentence-transformers all-MiniLM-L6-v2 (default)
                   Sparse -- BM25 via rank_bm25 (fallback / --retriever bm25)
  Generator      : any LoRA adapter loadable from the experiments/ folder
  Prompt         : tokenizer.apply_chat_template with injected context block

Embedding cache
---------------
  First run encodes all 4,441 chunks (~30-60 s on CPU).
  Cache is written to data/rag_embeddings.npy + data/rag_chunk_index.json.
  Subsequent runs load from cache instantly (~1 s).
  Cache is invalidated if train.json is newer than the cache files.

Install (once, fine_tuning env):
  pip install sentence-transformers rank_bm25 --break-system-packages

Usage
-----
  # Interactive REPL with the confirmed best adapter:
  python rag_inference.py --adapter_path experiments/10cat_4bit_r16_lr1e4_p3_20260506_012852/adapter

  # Single question, show what was retrieved:
  python rag_inference.py --adapter_path experiments/.../adapter --question "How do I treat anaphylaxis?" --show_context

  # Batch over the 40-question eval bank, save results:
  python rag_inference.py --adapter_path experiments/.../adapter --batch --questions_file data/eval_questions_40.json --out rag_results.json

  # Use BM25 retriever instead of dense:
  python rag_inference.py --adapter_path experiments/.../adapter --retriever bm25

  # No adapter (base model only, for comparison):
  python rag_inference.py --base_only --question "CPR ratio?"
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np
import torch
from peft import PeftModel
from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

HERE         = os.path.dirname(os.path.abspath(__file__))
MODEL_ID     = "google/gemma-2b-it"
LOCAL_MODEL  = os.path.join(HERE, "models", "gemma-2b-it")
TRAIN_SPLIT  = os.path.join(HERE, "splits", "10cat", "train.json")
CACHE_EMB    = os.path.join(HERE, "data", "rag_embeddings.npy")
CACHE_IDX    = os.path.join(HERE, "data", "rag_chunk_index.json")
EMBED_MODEL  = "sentence-transformers/all-MiniLM-L6-v2"
DEFAULT_TOPK = 3

SYSTEM_PROMPT = (
    "You are a first aid assistant. Provide accurate, step-by-step emergency "
    "guidance. For life-threatening situations, always advise calling emergency "
    "services immediately."
)

# ---------------------------------------------------------------------------
# Dependency checks
# ---------------------------------------------------------------------------

def _check_sentence_transformers() -> bool:
    try:
        import sentence_transformers  # noqa: F401
        return True
    except ImportError:
        return False


def _check_bm25() -> bool:
    try:
        import rank_bm25  # noqa: F401
        return True
    except ImportError:
        return False


# ---------------------------------------------------------------------------
# Knowledge base
# ---------------------------------------------------------------------------

class KnowledgeBase:
    """
    Loads train.json and exposes a flat list of text chunks.
    Each chunk is formatted as a self-contained Q&A snippet with its category.
    """

    def __init__(self, splits_path: str = TRAIN_SPLIT):
        if not os.path.exists(splits_path):
            raise FileNotFoundError(
                f"Training split not found at {splits_path}. "
                "Run data.py first to generate splits."
            )
        samples = json.load(open(splits_path, encoding="utf-8"))
        self.chunks: List[str] = []
        self.metadata: List[dict] = []
        for s in samples:
            cat   = s.get("category", "General")
            q     = s["question"].strip()
            a     = s["answer"].strip()
            chunk = f"[{cat}]\nQ: {q}\nA: {a}"
            self.chunks.append(chunk)
            self.metadata.append({
                "category":       cat,
                "safety_critical": s.get("safety_critical", False),
                "question":       q,
            })
        print(f"[rag] Knowledge base loaded: {len(self.chunks):,} chunks from {splits_path}")

    def __len__(self):
        return len(self.chunks)

    def __getitem__(self, idx):
        return self.chunks[idx]


# ---------------------------------------------------------------------------
# Dense retriever  (sentence-transformers + cosine similarity)
# ---------------------------------------------------------------------------

class DenseRetriever:
    """
    Embeds all chunks at init (with disk cache) then retrieves by cosine similarity.
    Requires: pip install sentence-transformers
    """

    def __init__(self, kb: KnowledgeBase, model_name: str = EMBED_MODEL):
        from sentence_transformers import SentenceTransformer

        self.kb = kb
        print(f"[rag] Dense retriever -- loading embedding model: {model_name}")
        self.model = SentenceTransformer(model_name)

        # Cache validity: cache exists AND train.json is not newer than cache
        use_cache = (
            os.path.exists(CACHE_EMB)
            and os.path.exists(CACHE_IDX)
            and os.path.getmtime(TRAIN_SPLIT) <= os.path.getmtime(CACHE_EMB)
        )

        if use_cache:
            print("[rag] Loading embeddings from cache...")
            self.embeddings = np.load(CACHE_EMB)
            print(f"[rag] Loaded {self.embeddings.shape[0]:,} cached embeddings")
        else:
            print(f"[rag] Encoding {len(kb):,} chunks (first run -- ~30-60 s on CPU)...")
            t0 = time.time()
            self.embeddings = self.model.encode(
                kb.chunks,
                batch_size=64,
                show_progress_bar=True,
                normalize_embeddings=True,
            )
            elapsed = time.time() - t0
            print(f"[rag] Encoded in {elapsed:.1f}s. Saving cache...")
            os.makedirs(os.path.dirname(CACHE_EMB), exist_ok=True)
            np.save(CACHE_EMB, self.embeddings)
            with open(CACHE_IDX, "w") as f:
                json.dump([m["question"][:80] for m in kb.metadata], f, indent=2)
            print(f"[rag] Cache saved -> {CACHE_EMB}")

    def retrieve(self, query: str, top_k: int = DEFAULT_TOPK) -> List[Tuple[int, float]]:
        """Return list of (chunk_idx, score) sorted by descending relevance."""
        q_emb = self.model.encode([query], normalize_embeddings=True)
        scores = (self.embeddings @ q_emb.T).squeeze()
        top_indices = np.argsort(scores)[::-1][:top_k]
        return [(int(i), float(scores[i])) for i in top_indices]


# ---------------------------------------------------------------------------
# Sparse retriever  (BM25 fallback)
# ---------------------------------------------------------------------------

class BM25Retriever:
    """
    BM25 retrieval over chunk text.
    Requires: pip install rank_bm25
    """

    def __init__(self, kb: KnowledgeBase):
        from rank_bm25 import BM25Okapi
        self.kb = kb
        print("[rag] BM25 retriever -- indexing chunks...")
        tokenized = [chunk.lower().split() for chunk in kb.chunks]
        self.index = BM25Okapi(tokenized)
        print(f"[rag] BM25 index built over {len(kb):,} chunks")

    def retrieve(self, query: str, top_k: int = DEFAULT_TOPK) -> List[Tuple[int, float]]:
        tokens = query.lower().split()
        scores = self.index.get_scores(tokens)
        top_indices = np.argsort(scores)[::-1][:top_k]
        return [(int(i), float(scores[i])) for i in top_indices]


# ---------------------------------------------------------------------------
# Model loading  (mirrors inference.py / eval_suite.py patterns)
# ---------------------------------------------------------------------------

def resolve_model(model_path: str = "") -> Tuple[str, bool]:
    for candidate in [p for p in [model_path, LOCAL_MODEL] if p]:
        if os.path.isdir(candidate) and os.path.exists(
            os.path.join(candidate, "config.json")
        ):
            return os.path.abspath(candidate), True
    return MODEL_ID, False


def load_model_and_tokenizer(
    quant: str = "4bit",
    model_path: str = "",
    adapter_path: str = "",
):
    source, is_local = resolve_model(model_path)
    print(f"[rag] Loading model: {source}  (quant={quant})")

    bnb_config = None
    if quant == "4bit":
        bnb_config = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_use_double_quant=True,
            bnb_4bit_compute_dtype=torch.float16,
        )
    elif quant == "8bit":
        bnb_config = BitsAndBytesConfig(load_in_8bit=True)

    tokenizer = AutoTokenizer.from_pretrained(
        source, trust_remote_code=True, local_files_only=is_local
    )
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.unk_token
        tokenizer.pad_token_id = tokenizer.unk_token_id
    tokenizer.padding_side = "left"

    model = AutoModelForCausalLM.from_pretrained(
        source,
        quantization_config=bnb_config,
        device_map="auto",
        trust_remote_code=True,
        dtype=torch.float16,
        local_files_only=is_local,
    )

    if adapter_path:
        if not os.path.isdir(adapter_path):
            raise FileNotFoundError(f"Adapter not found: {adapter_path}")
        print(f"[rag] Loading adapter: {adapter_path}")
        model = PeftModel.from_pretrained(model, adapter_path)

    model.eval()
    return model, tokenizer


# ---------------------------------------------------------------------------
# RAG inference engine
# ---------------------------------------------------------------------------

class RAGInferenceEngine:
    """
    Combines a retriever and a loaded model to answer first-aid questions
    with grounded context injected before generation.
    """

    def __init__(self, model, tokenizer, retriever, top_k: int = DEFAULT_TOPK):
        self.model     = model
        self.tokenizer = tokenizer
        self.retriever = retriever
        self.top_k     = top_k

    def _build_context_block(self, results: List[Tuple[int, float]]) -> str:
        lines = []
        for rank, (idx, score) in enumerate(results, 1):
            chunk = self.retriever.kb.chunks[idx]
            lines.append(f"[Reference {rank}]\n{chunk}")
        return "\n\n".join(lines)

    def _build_prompt(self, question: str, context: str) -> str:
        user_content = (
            SYSTEM_PROMPT
            + "\n\nRelevant first aid references:\n"
            + context
            + "\n\n"
            + question
        )
        messages = [{"role": "user", "content": user_content}]
        return self.tokenizer.apply_chat_template(
            messages,
            tokenize=False,
            add_generation_prompt=True,
        )

    @torch.inference_mode()
    def answer(
        self,
        question: str,
        max_new_tokens: int = 300,
        temperature: float = 0.1,
        show_context: bool = False,
    ) -> dict:
        """
        Retrieve context, build prompt, generate answer.
        Returns dict with answer, retrieved chunks, and timing.
        """
        t_retrieve = time.time()
        results    = self.retriever.retrieve(question, self.top_k)
        t_retrieve = time.time() - t_retrieve

        context = self._build_context_block(results)
        prompt  = self._build_prompt(question, context)

        inputs = self.tokenizer(
            prompt,
            return_tensors="pt",
            add_special_tokens=False,
        ).to(self.model.device)

        t_gen = time.time()
        output_ids = self.model.generate(
            **inputs,
            max_new_tokens=max_new_tokens,
            temperature=temperature,
            do_sample=temperature > 0,
            pad_token_id=self.tokenizer.pad_token_id,
            eos_token_id=self.tokenizer.eos_token_id,
        )
        t_gen = time.time() - t_gen

        new_tokens = output_ids[0][inputs["input_ids"].shape[-1]:]
        answer_text = self.tokenizer.decode(new_tokens, skip_special_tokens=True).strip()

        retrieved_chunks = [
            {
                "rank":     rank,
                "score":    round(score, 4),
                "category": self.retriever.kb.metadata[idx]["category"],
                "question": self.retriever.kb.metadata[idx]["question"],
                "chunk":    self.retriever.kb.chunks[idx],
            }
            for rank, (idx, score) in enumerate(results, 1)
        ]

        if show_context:
            print("\n  -- Retrieved context --")
            for c in retrieved_chunks:
                print(f"  [{c['rank']}] score={c['score']:.3f}  {c['category']}")
                print(f"      {c['question'][:90]}")
            print("  -- End context --\n")

        return {
            "question":         question,
            "answer":           answer_text,
            "retrieved_chunks": retrieved_chunks,
            "retrieve_time_s":  round(t_retrieve, 3),
            "generate_time_s":  round(t_gen, 3),
        }


# ---------------------------------------------------------------------------
# Mode: single question
# ---------------------------------------------------------------------------

def run_single(engine: RAGInferenceEngine, question: str, show_context: bool):
    print(f"\nQ: {question}")
    result = engine.answer(question, show_context=show_context)
    print(f"A: {result['answer']}")
    print(f"\n   Retrieve: {result['retrieve_time_s']}s  |  Generate: {result['generate_time_s']}s")


# ---------------------------------------------------------------------------
# Mode: interactive REPL
# ---------------------------------------------------------------------------

def run_interactive(engine: RAGInferenceEngine, show_context: bool):
    print("\nRAG First Aid Assistant  (type 'quit' or Ctrl-C to exit)\n")
    while True:
        try:
            q = input("Question: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nExiting.")
            break
        if not q or q.lower() in ("quit", "exit", "q"):
            break
        result = engine.answer(q, show_context=show_context)
        print(f"\nAnswer: {result['answer']}")
        if show_context:
            pass  # already printed inside engine.answer
        print(f"\n[retrieve {result['retrieve_time_s']}s | generate {result['generate_time_s']}s]\n")


# ---------------------------------------------------------------------------
# Mode: batch over eval question bank
# ---------------------------------------------------------------------------

def run_batch(
    engine: RAGInferenceEngine,
    questions_file: str,
    out_path: str,
    show_context: bool,
):
    if not os.path.exists(questions_file):
        print(f"[rag] Questions file not found: {questions_file}")
        sys.exit(1)

    bank = json.load(open(questions_file, encoding="utf-8"))
    questions = bank.get("questions", bank) if isinstance(bank, dict) else bank

    print(f"\n[rag] Batch mode -- {len(questions)} questions from {questions_file}")
    print(f"[rag] Results will be saved to: {out_path}\n")

    results = []
    for i, item in enumerate(questions, 1):
        q   = item["question"]
        ref = item.get("reference", "")
        cat = item.get("category", "")
        sc  = item.get("safety_critical", False)

        print(f"  Q{i:02d}/{len(questions)}  {q[:70]}...")
        result = engine.answer(q, show_context=show_context)

        results.append({
            "id":               item.get("id", i),
            "category":         cat,
            "safety_critical":  sc,
            "question":         q,
            "reference":        ref,
            "rag_answer":       result["answer"],
            "retrieved_chunks": result["retrieved_chunks"],
            "retrieve_time_s":  result["retrieve_time_s"],
            "generate_time_s":  result["generate_time_s"],
        })
        print(f"       {result['generate_time_s']}s  |  {result['answer'][:80]}...")

    output = {
        "source":         questions_file,
        "adapter":        engine.model.active_adapter if hasattr(engine.model, "active_adapter") else "base",
        "top_k":          engine.top_k,
        "retriever_type": type(engine.retriever).__name__,
        "n_questions":    len(results),
        "results":        results,
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    print(f"\n[rag] Batch complete. Results saved -> {out_path}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args():
    p = argparse.ArgumentParser(
        description="RAG-augmented inference for first-aid LoRA adapters",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    # Model
    p.add_argument("--model_path",    default="",
                   help="Local model directory (default: models/gemma-2b-it)")
    p.add_argument("--adapter_path",  default="",
                   help="Path to LoRA adapter directory inside experiments/")
    p.add_argument("--quant",         default="4bit", choices=["4bit", "8bit", "fp16"],
                   help="Quantization mode (default: 4bit)")
    p.add_argument("--base_only",     action="store_true",
                   help="Load base model without any adapter")

    # Knowledge base / retriever
    p.add_argument("--splits_path",   default=TRAIN_SPLIT,
                   help=f"Path to train.json (default: {TRAIN_SPLIT})")
    p.add_argument("--retriever",     default="dense", choices=["dense", "bm25"],
                   help="Retriever type: dense (sentence-transformers) or bm25 (default: dense)")
    p.add_argument("--top_k",         type=int, default=DEFAULT_TOPK,
                   help=f"Number of chunks to retrieve (default: {DEFAULT_TOPK})")

    # Mode
    p.add_argument("--question",      default="",
                   help="Single question to answer (default: interactive mode)")
    p.add_argument("--interactive",   action="store_true",
                   help="Start interactive REPL (default if no --question/--batch)")
    p.add_argument("--batch",         action="store_true",
                   help="Batch mode: run over --questions_file")
    p.add_argument("--questions_file",default=os.path.join(HERE, "data", "eval_questions_40.json"),
                   help="Question bank for batch mode")
    p.add_argument("--out",           default=os.path.join(HERE, "evaluations", "rag_batch_results.json"),
                   help="Output path for batch results")

    # Generation
    p.add_argument("--max_new_tokens",type=int, default=300)
    p.add_argument("--temperature",   type=float, default=0.1)
    p.add_argument("--show_context",  action="store_true",
                   help="Print retrieved reference chunks for each query")

    return p.parse_args()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    args = parse_args()

    print("=" * 60)
    print("  RAG First Aid Inference")
    print("=" * 60)
    print(f"  Retriever  : {args.retriever}  (top_k={args.top_k})")
    print(f"  Quant      : {args.quant}")
    print(f"  Adapter    : {args.adapter_path or '(none -- base model)'}")
    print(f"  Knowledge  : {args.splits_path}")
    print("=" * 60)

    # --- Build knowledge base ---
    kb = KnowledgeBase(args.splits_path)

    # --- Build retriever ---
    if args.retriever == "dense":
        if not _check_sentence_transformers():
            print(
                "[rag] sentence-transformers not installed.\n"
                "      Install with: pip install sentence-transformers --break-system-packages\n"
                "      Falling back to BM25."
            )
            if not _check_bm25():
                print("[rag] rank_bm25 also not installed. Install either dependency and retry.")
                sys.exit(1)
            retriever = BM25Retriever(kb)
        else:
            retriever = DenseRetriever(kb)
    else:
        if not _check_bm25():
            print(
                "[rag] rank_bm25 not installed.\n"
                "      Install with: pip install rank_bm25 --break-system-packages"
            )
            sys.exit(1)
        retriever = BM25Retriever(kb)

    # --- Load model ---
    adapter = None if args.base_only else (args.adapter_path or None)
    model, tokenizer = load_model_and_tokenizer(
        quant=args.quant,
        model_path=args.model_path,
        adapter_path=adapter or "",
    )

    # --- Build engine ---
    engine = RAGInferenceEngine(
        model=model,
        tokenizer=tokenizer,
        retriever=retriever,
        top_k=args.top_k,
    )

    # Monkey-patch max_new_tokens and temperature into answer()
    _orig_answer = engine.answer
    def _patched_answer(question, **kwargs):
        kwargs.setdefault("max_new_tokens", args.max_new_tokens)
        kwargs.setdefault("temperature",    args.temperature)
        return _orig_answer(question, **kwargs)
    engine.answer = _patched_answer

    # --- Dispatch mode ---
    if args.batch:
        run_batch(engine, args.questions_file, args.out, args.show_context)
    elif args.question:
        run_single(engine, args.question, args.show_context)
    else:
        # Default: interactive
        run_interactive(engine, args.show_context)


if __name__ == "__main__":
    main()
