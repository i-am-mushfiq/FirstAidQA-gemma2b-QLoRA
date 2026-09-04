"""Regression tests for the immutable camera-ready evaluation contracts."""

import json
import tempfile
import unittest
from pathlib import Path

from bm25_rag import BM25Retriever
from build_v2_judge_prompt import RUBRIC, _meta_summary, build_prompt
from camera_ready.check import DEFAULT_MANIFEST, collect_checks, load_protocol
from camera_ready.pipeline import build_parser, generation_command
from evaluation_protocol import (
    CAMERA_READY_CONFIG_RESOLUTION,
    PROMPT_POLICY,
    default_analysis_dir,
    prompt_metadata,
    question_bank_metadata,
    validate_run_question_provenance,
)
from judge_per_item import JUDGES, score_input_sha256, validate_response
from stats_v2 import CAMERA_READY_CONFIGS, load_scores, spearman_rho


ROOT = Path(__file__).parent
BANK_PATH = ROOT / "evaluations" / "eval_bank_v2_40q" / "eval_bank_v2.json"


def make_run() -> tuple[dict, list[dict]]:
    bank = json.loads(BANK_PATH.read_text(encoding="utf-8"))
    variants = {}
    for config in CAMERA_READY_CONFIGS:
        answers = []
        for question in bank:
            answers.append({
                **question,
                "prompt_policy": PROMPT_POLICY,
                "config_resolution": CAMERA_READY_CONFIG_RESOLUTION[config],
                "answer": f"Answer for {question['question_id']}",
                "meta": {},
            })
        variants[config] = {"n": len(answers), "answers": answers}
    run = {
        "run_type": "v2_comprehensive",
        "run_args": {
            "_prompt": prompt_metadata(),
            "_question_bank": question_bank_metadata(bank, str(BANK_PATH)),
            "_config_resolution": {
                key: CAMERA_READY_CONFIG_RESOLUTION[key]
                for key in sorted(CAMERA_READY_CONFIGS)
            },
            "_artifacts": {
                "model": {"sha256": "test"},
                "adapter_4bit": {"sha256": "test"},
            },
        },
        "variants": variants,
    }
    return run, bank


class FakeIndex:
    def __init__(self, scores):
        self.scores = scores

    def get_scores(self, _tokens):
        import numpy as np
        return np.array(self.scores, dtype=float)


def fake_retriever(scores) -> BM25Retriever:
    retriever = BM25Retriever.__new__(BM25Retriever)
    retriever.gap_gate = False
    retriever.available = True
    retriever.verbose = False
    retriever._index = FakeIndex(scores)
    retriever._questions = ["alpha", "beta"]
    retriever._answers = ["first", "second"]
    retriever._categories = ["cat1", "cat2"]
    return retriever


class CameraReadyPipelineTests(unittest.TestCase):
    def test_facade_manifest_matches_canonical_contract(self):
        protocol = load_protocol(DEFAULT_MANIFEST)
        self.assertEqual(protocol["prompt_policy"], PROMPT_POLICY)
        self.assertEqual(protocol["generation"]["config_codes"], ["A", "B", "C", "E", "F", "G"])
        command = generation_command(protocol)
        self.assertIn("--adapter_4bit", command)
        self.assertNotIn("--adapter_8bit", command)
        config_at = command.index("--configs")
        max_tokens_at = command.index("--max_new_tokens")
        self.assertEqual(command[config_at + 1:max_tokens_at], ["A", "B", "C", "E", "F", "G"])

    def test_facade_checker_is_usable_without_runtime_dependency_probe(self):
        results = collect_checks(
            strict=False, check_dependencies=False, run_tests=False
        )
        names = {item.name for item in results}
        self.assertIn("runtime rubric", names)
        self.assertIn("config C", names)
        self.assertFalse(any(item.level == "error" for item in results))

    def test_facade_exposes_all_stages(self):
        parser = build_parser()
        for command in ["check", "generate", "verify", "judge", "analyze", "manual-prompt", "status"]:
            parsed = parser.parse_args([command])
            self.assertEqual(parsed.command, command)

    def test_frozen_question_hash_and_cross_config_consistency(self):
        run, _ = make_run()
        self.assertEqual(validate_run_question_provenance(run), [])
        run["variants"]["G_BASE_RAG"]["answers"][0]["reference"] += " changed"
        self.assertTrue(validate_run_question_provenance(run))

    def test_analysis_directory_is_a_sibling(self):
        run_dir = ROOT / "evaluations" / "CAMERA_READY_OFFLINE_20990101_000000"
        self.assertEqual(
            default_analysis_dir(run_dir),
            run_dir.parent / f"{run_dir.name}_ANALYSIS",
        )

    def test_bm25_uses_raw_positive_score_and_skips_no_match(self):
        hit = fake_retriever([0.25, 2.5]).retrieve("beta")
        self.assertTrue(hit["bm25_fired"])
        self.assertEqual(hit["score"], 2.5)
        self.assertEqual(hit["score_kind"], "bm25_raw")
        miss = fake_retriever([-0.5, 0.0]).retrieve("unknown")
        self.assertFalse(miss["bm25_fired"])
        self.assertTrue(miss["bm25_no_positive_match"])

    def test_builder_reads_current_rag_metadata(self):
        summary = _meta_summary("F_RAG_BM25", {
            "bm25_fired": True,
            "bm25_skipped_gap": False,
            "retrieved_category": "bleeding",
            "retrieved_question": "How to control bleeding?",
            "retrieved_score": 3.25,
            "retrieve_time_s": 0.01,
        })
        self.assertIn("fired=True", summary)
        self.assertIn("category=bleeding", summary)
        self.assertIn("bm25_raw=3.25", summary)

    def test_builder_uses_frozen_run_without_live_bank(self):
        run, _ = make_run()
        with tempfile.TemporaryDirectory() as temp:
            run_dir = Path(temp)
            (run_dir / "run.json").write_text(json.dumps(run), encoding="utf-8")
            prompt = build_prompt(str(run_dir))
        self.assertIn("SC: 11 (27%)", prompt)
        self.assertIn("BASE8 + FT4_ADAPTER", prompt)

    def test_judge_schema_is_strict(self):
        self.assertEqual(validate_response({
            "score": 3,
            "override_triggered": "none",
            "rationale": "Concise valid rationale.",
        }), [])
        self.assertTrue(validate_response({
            "score": True,
            "override_triggered": "anything",
            "rationale": "word " * 51,
        }))

    def test_cache_hash_changes_with_any_scoring_input(self):
        run, bank = make_run()
        q = bank[0]
        base = score_input_sha256("gpt4o", q, "answer", RUBRIC, "A_BASE_4BIT")
        self.assertNotEqual(
            base,
            score_input_sha256("gpt4o", q, "changed", RUBRIC, "A_BASE_4BIT"),
        )
        self.assertNotEqual(
            base,
            score_input_sha256("gpt4o", q, "answer", RUBRIC + "x", "A_BASE_4BIT"),
        )

    def test_spearman_handles_ties(self):
        self.assertAlmostEqual(spearman_rho([1, 1, 2], [1, 2, 2]), 0.5)

    def test_stats_require_complete_current_panel(self):
        run, bank = make_run()
        with tempfile.TemporaryDirectory() as temp:
            analysis_dir = Path(temp)
            for config in CAMERA_READY_CONFIGS:
                answers = {
                    item["question_id"]: item["answer"]
                    for item in run["variants"][config]["answers"]
                }
                for question in bank:
                    qid = question["question_id"]
                    for judge in JUDGES:
                        path = analysis_dir / "judgments" / judge / config / f"{qid}.json"
                        path.parent.mkdir(parents=True, exist_ok=True)
                        payload = {
                            "score": 3,
                            "judge_id": judge,
                            "config_label": config,
                            "question_id": qid,
                            "input_sha256": score_input_sha256(
                                judge, question, answers[qid], RUBRIC, config
                            ),
                        }
                        path.write_text(json.dumps(payload), encoding="utf-8")
            scores = load_scores(analysis_dir, bank, run, RUBRIC)
            self.assertEqual(len(scores["A_BASE_4BIT"]), len(bank))
            missing = analysis_dir / "judgments" / "gpt4o" / "A_BASE_4BIT" / "V2Q01.json"
            missing.unlink()
            with self.assertRaisesRegex(ValueError, "incomplete or stale"):
                load_scores(analysis_dir, bank, run, RUBRIC)


if __name__ == "__main__":
    unittest.main()
