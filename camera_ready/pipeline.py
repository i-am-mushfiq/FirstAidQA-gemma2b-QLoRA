"""Single operator-facing CLI for the camera-ready evaluation lifecycle."""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from camera_ready.check import DEFAULT_MANIFEST, collect_checks, load_protocol, print_report
except ModuleNotFoundError:  # Direct execution from inside camera_ready/.
    from check import DEFAULT_MANIFEST, collect_checks, load_protocol, print_report

from evaluation_protocol import (  # noqa: E402
    CAMERA_CONFIG_CODES,
    CAMERA_READY_CONFIG_RESOLUTION,
    default_analysis_dir,
    validate_camera_config_resolution,
    validate_run_prompt_provenance,
    validate_run_question_provenance,
)


def repo_path(relative: str) -> Path:
    path = (ROOT / relative).resolve()
    path.relative_to(ROOT)
    return path


def newest_run(protocol: dict) -> Path | None:
    prefix = protocol["generation"]["output_prefix"]
    candidates = sorted(
        path for path in (ROOT / "evaluations").glob(f"{prefix}*")
        if path.is_dir() and not path.name.endswith("_ANALYSIS")
        and (path / "run.json").exists()
    )
    return candidates[-1] if candidates else None


def resolve_run(value: str | None, protocol: dict) -> Path:
    path = repo_path(value) if value else newest_run(protocol)
    if path is None:
        raise ValueError("no camera-ready offline run exists")
    if not (path / "run.json").exists():
        raise ValueError(f"run.json not found in {path}")
    return path


def generation_command(protocol: dict) -> list[str]:
    generation = protocol["generation"]
    return [
        sys.executable,
        str(repo_path(generation["script"])),
        "--model_path", str(repo_path(generation["model"])),
        "--adapter_4bit", str(repo_path(generation["adapter_4bit"])),
        "--questions", str(repo_path(generation["questions"])),
        "--configs", *generation["config_codes"],
        "--max_new_tokens", str(generation["max_new_tokens"]),
        "--prompt_policy", protocol["prompt_policy"],
        "--camera_ready",
    ]


def run_command(command: list[str]) -> int:
    print("\n> " + " ".join(command))
    return subprocess.run(command, cwd=ROOT).returncode


def run_health_check(protocol_path: Path, *, strict: bool) -> int:
    results = collect_checks(strict=strict, manifest_path=protocol_path)
    print_report(results)
    return 1 if any(item.level == "error" for item in results) else 0


def command_generate(protocol: dict, protocol_path: Path, commit: bool) -> int:
    if run_health_check(protocol_path, strict=True):
        print("\nGeneration blocked. Resolve the failed checks and run again.")
        return 1

    evaluations = ROOT / "evaluations"
    prefix = protocol["generation"]["output_prefix"]
    before = {path.resolve() for path in evaluations.glob(f"{prefix}*") if path.is_dir()}
    code = run_command(generation_command(protocol))
    if code:
        return code
    after = {path.resolve() for path in evaluations.glob(f"{prefix}*") if path.is_dir()}
    created = sorted(after - before)
    if len(created) != 1:
        print(f"Expected exactly one new run directory; found {len(created)}.")
        return 1
    run_dir = created[0]
    code = run_command([
        sys.executable,
        str(repo_path(protocol["verification"]["script"])),
        "--run_dir", str(run_dir),
    ])
    if code:
        print(f"Generation exists at {run_dir}, but verification did not clear it.")
        return code

    if commit:
        relative_run = run_dir.relative_to(ROOT)
        if run_command(["git", "add", str(relative_run)]):
            return 1
        message = (
            f"CAMERA_READY_OFFLINE: verified {run_dir.name}\n\n"
            f"Protocol: {protocol['name']}\n"
            f"Configs: {' '.join(protocol['generation']['config_codes'])}\n"
            f"Questions: {protocol['verification']['expected_questions']}"
        )
        if run_command(["git", "commit", "-m", message]):
            return 1
        print("Run committed locally. Push remains an explicit user action.")
    else:
        print("Run verified but not committed. Use --commit if a local run commit is desired.")

    print(f"\nRun:      {run_dir}")
    print(f"Analysis: {default_analysis_dir(run_dir)}")
    print(f"Next: {sys.executable} camera_ready/pipeline.py judge --run {run_dir.relative_to(ROOT)}")
    return 0


def command_verify(protocol: dict, run_value: str | None) -> int:
    run_dir = resolve_run(run_value, protocol)
    return run_command([
        sys.executable,
        str(repo_path(protocol["verification"]["script"])),
        "--run_dir", str(run_dir),
    ])


def command_judge(protocol: dict, args: argparse.Namespace) -> int:
    run_dir = resolve_run(args.run, protocol)
    command = [
        sys.executable,
        str(repo_path(protocol["judging"]["script"])),
        "--run_dir", str(run_dir),
    ]
    if args.judges:
        command += ["--judges", *args.judges]
    if args.configs:
        labels = [CAMERA_CONFIG_CODES.get(value, value) for value in args.configs]
        command += ["--configs", *labels]
    if args.rubric:
        command += ["--rubric", str(repo_path(args.rubric))]
    if args.pairwise:
        command.append("--pairwise")
    if args.dry_run:
        command.append("--dry_run")
    return run_command(command)


def command_analyze(protocol: dict, args: argparse.Namespace) -> int:
    run_dir = resolve_run(args.run, protocol)
    command = [
        sys.executable,
        str(repo_path(protocol["analysis"]["script"])),
        "--run_dir", str(run_dir),
    ]
    if args.rubric:
        command += ["--rubric", str(repo_path(args.rubric))]
    if args.judges:
        command += ["--judges", *args.judges]
    return run_command(command)


def command_manual_prompt(protocol: dict, args: argparse.Namespace) -> int:
    run_dir = resolve_run(args.run, protocol)
    command = [
        sys.executable,
        str(repo_path(protocol["optional_manual_protocol"]["script"])),
        "--run_dir", str(run_dir),
    ]
    if args.group:
        command += ["--group", str(args.group)]
    return run_command(command)


def _completion_summary(analysis_dir: Path) -> str:
    path = analysis_dir / "completion_matrix.csv"
    if not path.exists():
        return "not started"
    try:
        with path.open(encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        complete = sum(row.get("pct") == "100.0%" for row in rows)
        return f"{complete}/{len(rows)} judge-config cells complete"
    except (OSError, csv.Error):
        return "completion matrix unreadable"


def command_status(protocol: dict) -> int:
    codes = protocol["generation"]["config_codes"]
    print(f"Protocol: {protocol['name']} ({protocol['prompt_policy']})")
    print(f"Configs:  {' '.join(codes)}")
    for code in codes:
        label = CAMERA_CONFIG_CODES[code]
        resolution = CAMERA_READY_CONFIG_RESOLUTION[label]
        print(f"  {code}: {label} = {resolution['base_quant']} / "
              f"{resolution['adapter'] or 'no adapter'} / {resolution['technique']}")
    run_dir = newest_run(protocol)
    if run_dir is None:
        print("Latest run: none")
        return 0
    run = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    problems = (
        validate_run_prompt_provenance(run)
        + validate_run_question_provenance(run)
        + validate_camera_config_resolution(run)
    )
    state = "aligned" if not problems else "legacy or invalid"
    analysis_dir = default_analysis_dir(run_dir)
    print(f"Latest run: {run_dir.name} ({state})")
    if problems:
        for problem in problems[:5]:
            print(f"  - {problem}")
    print(f"Analysis:   {analysis_dir.name} ({_completion_summary(analysis_dir)})")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Canonical, non-destructive camera-ready pipeline facade"
    )
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    sub = parser.add_subparsers(dest="command", required=True)

    check_parser = sub.add_parser("check", help="read-only pipeline health report")
    check_parser.add_argument("--strict", action="store_true")

    generate = sub.add_parser("generate", help="generate and verify a new immutable run")
    generate.add_argument("--commit", action="store_true",
                          help="commit only the newly generated run after verification")

    verify = sub.add_parser("verify", help="verify a generated run")
    verify.add_argument("--run")

    judge = sub.add_parser("judge", help="run blinded per-item judging")
    judge.add_argument("--run")
    judge.add_argument("--judges", nargs="+")
    judge.add_argument("--configs", nargs="+")
    judge.add_argument("--rubric")
    judge.add_argument("--pairwise", action="store_true")
    judge.add_argument("--dry-run", action="store_true")

    analyze = sub.add_parser("analyze", help="analyze a complete judgment panel")
    analyze.add_argument("--run")
    analyze.add_argument("--rubric")
    analyze.add_argument("--judges", nargs="+",
                         help="Panel subset to analyse (default: all six)")

    manual = sub.add_parser("manual-prompt", help="build the optional unblinded protocol")
    manual.add_argument("--run")
    manual.add_argument("--group", type=int, default=0)

    sub.add_parser("status", help="show protocol and latest-run status")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    manifest_path = Path(args.manifest).resolve()
    try:
        protocol = load_protocol(manifest_path)
        if args.command == "check":
            return run_health_check(manifest_path, strict=args.strict)
        if args.command == "generate":
            return command_generate(protocol, manifest_path, args.commit)
        if args.command == "verify":
            return command_verify(protocol, args.run)
        if args.command == "judge":
            return command_judge(protocol, args)
        if args.command == "analyze":
            return command_analyze(protocol, args)
        if args.command == "manual-prompt":
            return command_manual_prompt(protocol, args)
        if args.command == "status":
            return command_status(protocol)
    except (KeyError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
