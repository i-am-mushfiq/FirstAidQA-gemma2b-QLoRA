"""
Offline regression tests for the judging harness contracts.

No API calls, no network, no judge SDK required: the openai module is stubbed
before import so these run anywhere, including CI without provider credentials.

    python -m unittest -q judging/test_judging_harness.py
"""

import json
import sys
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# judge_deepseek.py exits at import time when the openai SDK is absent, so give
# it a stub. Nothing in these tests reaches a real client.
if "openai" not in sys.modules:
    _stub = types.ModuleType("openai")
    _stub.OpenAI = object
    sys.modules["openai"] = _stub

from judging import judge_deepseek as jd  # noqa: E402
from judging.aggregate import build_stats  # noqa: E402


class StatusError(Exception):
    """Stands in for openai.APIStatusError, which carries a numeric status_code."""

    def __init__(self, message, status_code):
        super().__init__(message)
        self.status_code = status_code


class RetryClassificationTests(unittest.TestCase):
    def test_status_code_drives_the_decision(self):
        self.assertTrue(jd._is_retryable(StatusError("rate limited", 429)))
        self.assertTrue(jd._is_retryable(StatusError("bad gateway", 502)))
        self.assertTrue(jd._is_retryable(StatusError("unavailable", 503)))
        self.assertFalse(jd._is_retryable(StatusError("bad request", 400)))
        self.assertFalse(jd._is_retryable(StatusError("unauthorized", 401)))

    def test_sdk_message_form_is_classified_without_a_status_code(self):
        # The exact string form the SDK renders. The previous heuristic checked
        # str(e)[:3] == "Err" for a "5", so every 5xx took the non-retryable
        # path despite the documented backoff-on-5xx behaviour.
        self.assertTrue(jd._is_retryable(Exception("Error code: 503 - upstream busy")))
        self.assertTrue(jd._is_retryable(Exception("Error code: 500 - internal error")))
        self.assertTrue(jd._is_retryable(Exception("Error code: 429 - slow down")))
        self.assertTrue(jd._is_retryable(Exception("HTTP 502 from upstream")))
        self.assertFalse(jd._is_retryable(Exception("Error code: 400 - bad request")))

    def test_incidental_digits_do_not_trigger_a_retry(self):
        # "5" anywhere in the first three characters used to be enough, and a
        # bare \b5\d\d\b match is just as wrong: these are counts and versions.
        self.assertFalse(jd._is_retryable(Exception("512 tokens exceeds the limit")))
        self.assertFalse(jd._is_retryable(Exception("model gpt-5.6-sol is not available")))
        self.assertFalse(jd._is_retryable(Exception("context window of 500 exceeded")))


class RetryExhaustionTests(unittest.TestCase):
    def test_exhaustion_raises_instead_of_returning_none(self):
        """A sustained rate-limit must not return None into judge_item_sync."""
        calls = {"n": 0}

        class AlwaysRateLimited:
            class chat:  # noqa: N801 - mirrors the SDK's attribute shape
                class completions:
                    @staticmethod
                    def create(**_kwargs):
                        calls["n"] += 1
                        raise StatusError("Error code: 429 - slow down", 429)

        original_sleep = jd.time.sleep
        jd.time.sleep = lambda _seconds: None
        try:
            with self.assertRaises(RuntimeError) as ctx:
                jd.call_api_sync(AlwaysRateLimited(), "test-model", "prompt")
        finally:
            jd.time.sleep = original_sleep

        self.assertIn("exhausted", str(ctx.exception))
        self.assertIsInstance(ctx.exception.__cause__, StatusError)
        self.assertEqual(calls["n"], jd.MAX_RETRIES + 2)

    def test_non_retryable_error_propagates_unchanged(self):
        class Unauthorized:
            class chat:  # noqa: N801
                class completions:
                    @staticmethod
                    def create(**_kwargs):
                        raise StatusError("Error code: 401 - bad key", 401)

        with self.assertRaises(StatusError):
            jd.call_api_sync(Unauthorized(), "test-model", "prompt")


def _rows(delta_per_question):
    """One paired row per question for configs X and Y, Y fixed at 3.0."""
    rows = []
    for i, delta in enumerate(delta_per_question, start=1):
        qid = f"V2Q{i:02d}"
        rows.append({"qid": qid, "config": "X", "quality_score": 3.0 + delta, "sc_flag": False})
        rows.append({"qid": qid, "config": "Y", "quality_score": 3.0, "sc_flag": False})
    return rows


class ContrastDirectionTests(unittest.TestCase):
    def test_significant_result_in_the_wrong_direction_is_flagged(self):
        rows = _rows([-2.0] * 20)
        contrast = {"name": "X-Y", "cfg_a": "X", "cfg_b": "Y", "filter": "all",
                    "primary": True, "expected_direction": "positive"}
        row = build_stats(rows, [contrast])[0]
        # `confirmed` stays exactly as PRECOMMIT.md defines it: two-sided.
        self.assertTrue(row["confirmed"])
        self.assertEqual(row["observed_direction"], "negative")
        self.assertFalse(row["direction_match"])

    def test_significant_result_in_the_predicted_direction_matches(self):
        rows = _rows([2.0] * 20)
        contrast = {"name": "X-Y", "cfg_a": "X", "cfg_b": "Y", "filter": "all",
                    "primary": True, "expected_direction": "positive"}
        row = build_stats(rows, [contrast])[0]
        self.assertTrue(row["confirmed"])
        self.assertTrue(row["direction_match"])

    def test_exploratory_contrast_declares_no_direction(self):
        rows = _rows([-2.0] * 20)
        contrast = {"name": "G-B", "cfg_a": "X", "cfg_b": "Y", "filter": "all",
                    "primary": False}
        row = build_stats(rows, [contrast])[0]
        self.assertEqual(row["expected_direction"], "either")
        self.assertTrue(row["direction_match"])

    def test_precommitted_primaries_all_declare_a_direction(self):
        from judging.aggregate import load_precommit_contrasts
        primaries = [c for c in load_precommit_contrasts() if c["primary"]]
        self.assertEqual(len(primaries), 3)
        for contrast in primaries:
            self.assertEqual(contrast["expected_direction"], "positive", contrast["name"])


class PanelCompletenessTests(unittest.TestCase):
    """A partial run must not produce a report that looks complete."""

    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.items = Path(self._tmp.name) / "items.jsonl"
        lines = []
        for cfg in ("A_BASE_4BIT", "B_FINETUNED_4BIT"):
            for i in range(1, 4):
                lines.append(json.dumps({"qid": f"V2Q{i:02d}", "config": cfg,
                                         "blind_id": f"BID_{cfg[:1]}{i}",
                                         "answer": "a"}))
        # A control item must not count toward the expected real-config grid.
        lines.append(json.dumps({"qid": "V2Q01", "config": "CTRL_REF",
                                 "blind_id": "BID_C1", "answer": "a"}))
        self.items.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    @staticmethod
    def _rows(configs, qids):
        return [{"qid": q, "config": c} for c in configs for q in qids]

    def test_complete_panel_passes_and_reports_an_item_set_hash(self):
        from judging.aggregate import assert_panel_complete
        rows = self._rows(("A_BASE_4BIT", "B_FINETUNED_4BIT"),
                          ("V2Q01", "V2Q02", "V2Q03"))
        coverage = assert_panel_complete(rows, {}, self.items)
        self.assertTrue(coverage["complete"])
        self.assertEqual(coverage["n_items_expected"], 6)
        self.assertEqual(coverage["n_items_scored"], 6)
        self.assertEqual(len(coverage["item_set_sha256"]), 64)

    def test_controls_only_run_is_refused(self):
        from judging.aggregate import assert_panel_complete
        with self.assertRaises(ValueError) as ctx:
            assert_panel_complete([], {}, self.items)
        self.assertIn("unscored", str(ctx.exception))

    def test_missing_config_is_refused(self):
        from judging.aggregate import assert_panel_complete
        rows = self._rows(("A_BASE_4BIT",), ("V2Q01", "V2Q02", "V2Q03"))
        with self.assertRaises(ValueError) as ctx:
            assert_panel_complete(rows, {}, self.items)
        self.assertIn("B_FINETUNED_4BIT", str(ctx.exception))

    def test_allow_partial_downgrades_to_a_warning(self):
        from judging.aggregate import assert_panel_complete
        coverage = assert_panel_complete([], {}, self.items, allow_partial=True)
        self.assertFalse(coverage["complete"])

    def test_item_set_hash_is_stable_and_order_independent(self):
        from judging.aggregate import assert_panel_complete
        first = assert_panel_complete([], {}, self.items, allow_partial=True)
        shuffled = self.items.read_text(encoding="utf-8").splitlines()
        shuffled.reverse()
        self.items.write_text("\n".join(shuffled) + "\n", encoding="utf-8")
        second = assert_panel_complete([], {}, self.items, allow_partial=True)
        self.assertEqual(first["item_set_sha256"], second["item_set_sha256"])


class ScoresTableTests(unittest.TestCase):
    """An unscreened safety pass must not read as 'no violations'."""

    @staticmethod
    def _judgment(qid, bid, prompt_type, **parsed):
        return {"qid": qid, "blind_id": bid, "prompt_type": prompt_type,
                "status": "ok", "parsed": parsed}

    def test_missing_safety_call_is_marked_unscreened_not_clean(self):
        from judging.aggregate import build_scores_table
        judgments = [
            self._judgment("V2Q01", "BID_A", "quality", score=4, rationale="ok"),
            # no safety judgment for this item
        ]
        rows = build_scores_table(judgments, {"V2Q01": {}}, {"BID_A": "A_BASE_4BIT"})
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0]["n_violations"])
        self.assertFalse(rows[0]["safety_screened"])

    def test_present_safety_call_is_counted(self):
        from judging.aggregate import build_scores_table
        judgments = [
            self._judgment("V2Q01", "BID_A", "quality", score=1, rationale="bad"),
            self._judgment("V2Q01", "BID_A", "safety",
                           violations={"SO05": True, "SO06": False}, quote="x"),
        ]
        rows = build_scores_table(judgments, {"V2Q01": {}}, {"BID_A": "A_BASE_4BIT"})
        self.assertEqual(rows[0]["n_violations"], 1)
        self.assertTrue(rows[0]["safety_screened"])
        self.assertEqual(rows[0]["violated_categories"], "SO05")

    def test_unmapped_blind_id_is_refused_rather_than_published(self):
        from judging.aggregate import build_scores_table
        judgments = [
            self._judgment("V2Q01", "BID_GHOST", "quality", score=3, rationale="x"),
        ]
        with self.assertRaises(ValueError) as ctx:
            build_scores_table(judgments, {"V2Q01": {}}, {"BID_A": "A_BASE_4BIT"})
        self.assertIn("blind_map.json", str(ctx.exception))

    def test_empty_stratum_reports_none_not_zero(self):
        from judging.aggregate import build_config_summary
        rows = [{"qid": "V2Q01", "config": "A_BASE_4BIT", "sc_flag": False,
                 "quality_score": 3, "n_violations": 0, "safety_screened": True}]
        summary = build_config_summary(rows)[0]
        self.assertEqual(summary["nonsc_mean"], 3.0)
        # No safety-critical rows at all: a mean of 0 would read as "scored 0
        # on every SC question", the opposite of "no SC data".
        self.assertIsNone(summary["sc_mean"])


class AssembleGateTests(unittest.TestCase):
    def test_config_expected_but_absent_fails_the_gate(self):
        from judging.assemble_items import validate_items, EXPECTED_N
        bank = {f"V2Q{i:02d}": {} for i in range(1, EXPECTED_N + 1)}
        items = [{"config": "A_BASE_4BIT", "qid": qid, "answer": "a"} for qid in bank]
        self.assertFalse(validate_items(items, bank,
                                        ["A_BASE_4BIT", "F_RAG_BM25"]))
        self.assertTrue(validate_items(items, bank, ["A_BASE_4BIT"]))

    def test_empty_answer_fails_the_gate(self):
        from judging.assemble_items import validate_items, EXPECTED_N
        bank = {f"V2Q{i:02d}": {} for i in range(1, EXPECTED_N + 1)}
        items = [{"config": "A_BASE_4BIT", "qid": qid, "answer": "a"} for qid in bank]
        items[0]["answer"] = "   "
        self.assertFalse(validate_items(items, bank, ["A_BASE_4BIT"]))


class ControlsKeyTests(unittest.TestCase):
    """The control key must not treat a correct judgment as a failure."""

    def test_vague_floor_allows_zero(self):
        """
        Regression guard for judging/OPEN_FINDINGS.md finding 4, now closed.

        controls_key.json was regenerated with `make_controls.py --key_only` on
        2026-09-05, so the floor is 0 and this passes. It was an
        expectedFailure tripwire until then. Keep it: a future regeneration
        that reintroduces a floor above 0 would silently start recording
        correct score-0 judgments as control failures again.
        """
        key = json.loads((REPO_ROOT / "judging" / "controls_key.json")
                         .read_text(encoding="utf-8"))
        vague = [k for k in key if k["control"] == "CTRL_VAGUE"]
        self.assertTrue(vague, "no CTRL_VAGUE entries in controls_key.json")
        bad = [k["qid"] for k in vague if k["expected_score_range"][0] > 0]
        self.assertEqual(
            bad, [],
            "CTRL_VAGUE lower bound must be 0: prompt_quality.txt defines 0 as "
            "'completely wrong / actively dangerous', which is what passive "
            "advice is during an emergency, so a judge scoring 0 is correct. "
            f"Regenerate controls_key.json via make_controls.py. Offending: {bad}"
        )


if __name__ == "__main__":
    unittest.main()
