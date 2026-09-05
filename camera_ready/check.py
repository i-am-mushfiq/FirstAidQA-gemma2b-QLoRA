"""Read-only health checks for the canonical camera-ready pipeline."""

from __future__ import annotations

import argparse
import ast
import importlib.util
import json
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from build_v2_judge_prompt import RUBRIC  # noqa: E402
from evaluation_protocol import (  # noqa: E402
    CAMERA_CONFIG_CODES,
    CAMERA_READY_CONFIG_RESOLUTION,
    CAMERA_SOURCE_FILES,
    PROMPT_POLICY,
)


DEFAULT_MANIFEST = Path(__file__).with_name("protocol.yaml")


@dataclass(frozen=True)
class CheckResult:
    level: str
    name: str
    detail: str


def load_protocol(path: str | Path = DEFAULT_MANIFEST) -> dict:
    """Load the JSON-compatible YAML manifest without adding a YAML dependency."""
    manifest_path = Path(path)
    try:
        value = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot load protocol manifest {manifest_path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError("protocol manifest must contain an object")
    return value


def resolve_repo_path(value: str) -> Path:
    path = (ROOT / value).resolve()
    try:
        path.relative_to(ROOT)
    except ValueError as exc:
        raise ValueError(f"manifest path escapes repository: {value}") from exc
    return path


def _result(condition: bool, name: str, success: str, failure: str,
            failure_level: str = "error") -> CheckResult:
    return CheckResult("pass" if condition else failure_level, name,
                       success if condition else failure)


def _importable(name: str) -> bool:
    """True if *name* can be located, False otherwise (never raises)."""
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def _git_source_status() -> tuple[bool, str]:
    proc = subprocess.run(
        ["git", "status", "--porcelain", "--", *CAMERA_SOURCE_FILES],
        cwd=ROOT, text=True, capture_output=True,
    )
    if proc.returncode != 0:
        return False, proc.stderr.strip() or "git status failed"
    dirty = proc.stdout.strip()
    return not bool(dirty), dirty or "canonical sources are committed and clean"


def collect_checks(*, strict: bool = False, check_dependencies: bool = True,
                   run_tests: bool = True,
                   manifest_path: str | Path = DEFAULT_MANIFEST) -> list[CheckResult]:
    """Collect checks without changing repository or evaluation state."""
    results: list[CheckResult] = []
    try:
        protocol = load_protocol(manifest_path)
    except ValueError as exc:
        return [CheckResult("error", "manifest", str(exc))]

    results.append(_result(
        protocol.get("schema_version") == 1,
        "manifest schema", "schema_version=1", "expected schema_version=1",
    ))
    results.append(_result(
        protocol.get("prompt_policy") == PROMPT_POLICY,
        "prompt policy", PROMPT_POLICY,
        f"manifest={protocol.get('prompt_policy')!r}; canonical={PROMPT_POLICY!r}",
    ))

    generation = protocol.get("generation", {})
    codes = generation.get("config_codes")
    results.append(_result(
        codes == list(CAMERA_CONFIG_CODES),
        "camera configs", " ".join(CAMERA_CONFIG_CODES),
        f"manifest={codes!r}; canonical={list(CAMERA_CONFIG_CODES)!r}",
    ))
    results.append(_result(
        CAMERA_CONFIG_CODES.get("C") in CAMERA_READY_CONFIG_RESOLUTION
        and CAMERA_READY_CONFIG_RESOLUTION[CAMERA_CONFIG_CODES["C"]] == {
            "base_quant": "8bit", "adapter": "adapter_4bit", "technique": "greedy"
        },
        "config C", "8-bit base + canonical 4-bit-trained adapter",
        "config C mapping differs from the camera-ready contract",
    ))

    configured_paths = {
        "generation script": generation.get("script"),
        "model": generation.get("model"),
        "4-bit adapter": generation.get("adapter_4bit"),
        "question bank": generation.get("questions"),
        "training split": generation.get("train_split"),
        "verification script": protocol.get("verification", {}).get("script"),
        "judging script": protocol.get("judging", {}).get("script"),
        "analysis script": protocol.get("analysis", {}).get("script"),
        "manual prompt script": protocol.get("optional_manual_protocol", {}).get("script"),
    }
    for name, value in configured_paths.items():
        if not isinstance(value, str) or not value:
            results.append(CheckResult("error", name, "path is missing from manifest"))
            continue
        try:
            path = resolve_repo_path(value)
        except ValueError as exc:
            results.append(CheckResult("error", name, str(exc)))
            continue
        results.append(_result(path.exists(), name, str(path), f"missing: {path}"))

    # Path existence is not sufficient. Weights and tokenizer vocabs are
    # gitignored, so both the model and the adapter survive in a fresh clone as
    # config-only shells: the dir-exists checks above pass on a machine where
    # generation cannot even load a tokenizer.
    for label, key, weight_globs in (
        ("model",         "model",        ("*.safetensors", "pytorch_model*.bin")),
        ("4-bit adapter", "adapter_4bit", ("adapter_model.safetensors", "adapter_model.bin")),
    ):
        try:
            directory = resolve_repo_path(generation[key])
        except (KeyError, TypeError, ValueError) as exc:
            results.append(CheckResult("error", f"{label} weights", str(exc)))
            continue
        weights = sorted(p for glob in weight_globs for p in directory.glob(glob))
        vocab = sorted(p for p in directory.glob("tokenizer.*")
                       if p.suffix in (".json", ".model"))
        results.append(_result(
            bool(weights), f"{label} weights",
            f"{weights[0].name} present" if weights else "",
            f"no {' / '.join(weight_globs)} in {directory} "
            f"(gitignored - restore before generating)",
        ))
        results.append(_result(
            bool(vocab), f"{label} tokenizer vocab",
            f"{vocab[0].name} present" if vocab else "",
            f"no tokenizer.json or tokenizer.model in {directory}; "
            f"GemmaTokenizer cannot load without one",
        ))

    for relative in CAMERA_SOURCE_FILES:
        path = ROOT / relative
        results.append(_result(
            path.exists(), f"source {relative}", "present", "missing",
        ))
        if path.suffix == ".py" and path.exists():
            try:
                ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
                results.append(CheckResult("pass", f"syntax {relative}", "valid Python"))
            except (OSError, SyntaxError) as exc:
                results.append(CheckResult("error", f"syntax {relative}", str(exc)))

    try:
        bank_path = resolve_repo_path(generation["questions"])
        bank = json.loads(bank_path.read_text(encoding="utf-8"))
        expected_n = protocol.get("verification", {}).get("expected_questions")
        ids = [question.get("question_id") for question in bank]
        expected_ids = [f"V2Q{i:02d}" for i in range(1, expected_n + 1)]
        results.append(_result(
            len(bank) == expected_n and ids == expected_ids,
            "question bank", f"{expected_n} ordered unique questions",
            f"expected {expected_n} ordered IDs V2Q01..V2Q{expected_n:02d}",
        ))
    except (KeyError, OSError, TypeError, json.JSONDecodeError) as exc:
        results.append(CheckResult("error", "question bank", str(exc)))

    rubric_path = ROOT / "rubric_v2.md"
    try:
        rubric_matches = RUBRIC.strip() in rubric_path.read_text(encoding="utf-8")
    except OSError:
        rubric_matches = False
    results.append(_result(
        rubric_matches, "runtime rubric", "matches documented final rubric",
        "build_v2_judge_prompt.RUBRIC differs from rubric_v2.md",
    ))

    clean, git_detail = _git_source_status()
    results.append(_result(
        clean, "source state", git_detail, git_detail,
        failure_level="error" if strict else "warn",
    ))

    if check_dependencies:
        # bitsandbytes belongs here: every camera-ready config loads the base
        # model in 4-bit or 8-bit, so without it generation dies at model load
        # while this check reported PASS.
        required = ["numpy", "torch", "transformers", "peft", "rank_bm25",
                    "bitsandbytes"]
        missing = [name for name in required if _importable(name) is False]
        results.append(_result(
            not missing, "generation dependencies", "all importable",
            "missing: " + ", ".join(missing),
            failure_level="error" if strict else "warn",
        ))
        # The judging and statistics stages have their own dependencies. Without
        # these, `check --strict` passed on a machine where scoring could not run.
        judging = ["openai", "scipy"]
        judging_missing = [name for name in judging if _importable(name) is False]
        results.append(_result(
            not judging_missing, "judging dependencies", "all importable",
            "missing: " + ", ".join(judging_missing)
            + " (needed by judging/judge_deepseek.py and the stats lanes)",
            failure_level="warn",
        ))

    if run_tests:
        proc = subprocess.run(
            [sys.executable, "-m", "unittest", "-q", "test_camera_ready_pipeline.py"],
            cwd=ROOT, text=True, capture_output=True,
        )
        detail = (proc.stdout + proc.stderr).strip()
        if proc.returncode == 0:
            detail = detail.splitlines()[-1] if detail else "contract tests passed"
        results.append(_result(
            proc.returncode == 0, "contract tests", detail, detail or "tests failed",
        ))

    return results


def print_report(results: list[CheckResult]) -> None:
    for item in results:
        marker = {"pass": "PASS", "warn": "WARN", "error": "FAIL"}[item.level]
        print(f"{marker:4}  {item.name}: {item.detail}")
    passes = sum(item.level == "pass" for item in results)
    warnings = sum(item.level == "warn" for item in results)
    errors = sum(item.level == "error" for item in results)
    print(f"\nSummary: {passes} passed, {warnings} warnings, {errors} errors")


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only camera-ready pipeline checks")
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    parser.add_argument("--strict", action="store_true",
                        help="Treat dirty sources and missing runtime dependencies as errors")
    parser.add_argument("--no-tests", action="store_true")
    parser.add_argument("--no-dependencies", action="store_true")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()
    results = collect_checks(
        strict=args.strict,
        check_dependencies=not args.no_dependencies,
        run_tests=not args.no_tests,
        manifest_path=args.manifest,
    )
    if args.as_json:
        print(json.dumps([asdict(item) for item in results], indent=2))
    else:
        print_report(results)
    return 1 if any(item.level == "error" for item in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
