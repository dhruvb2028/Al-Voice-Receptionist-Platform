"""Suite entrypoint: ``python -m ai_evals.cli``.

Exit codes are what CI keys on:

* ``0`` — every case passed.
* ``1`` — cases failed, but nothing safety-critical regressed.
* ``2`` — a safety property regressed or a safety-critical case failed.
  This is a separate code so a pipeline can treat safety as a hard gate
  while tolerating an in-progress quality failure.
"""

import argparse
import json
import sys
from pathlib import Path

from ai_evals.cases import load_cases, missing_scenarios
from ai_evals.report import build_report, compare_runs, render_html, render_json
from ai_evals.runner import run_suite

#: src/ai_evals/cli.py -> src/ai_evals -> src -> packages/evals
DEFAULT_CASES = Path(__file__).resolve().parents[2] / "cases"

EXIT_OK = 0
EXIT_FAILURES = 1
EXIT_SAFETY = 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the receptionist evaluation suite.")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--out", type=Path, default=Path("eval-results"))
    parser.add_argument(
        "--baseline",
        type=Path,
        default=None,
        help="previous report.json to compare against",
    )
    parser.add_argument(
        "--require-coverage",
        action="store_true",
        help="fail when a required scenario has no case",
    )
    args = parser.parse_args(argv)

    cases = load_cases(args.cases)
    if not cases:
        print(f"No cases found in {args.cases}", file=sys.stderr)
        return EXIT_FAILURES

    if args.require_coverage:
        missing = missing_scenarios(cases)
        if missing:
            print(f"Missing required scenarios: {', '.join(missing)}", file=sys.stderr)
            return EXIT_SAFETY

    results = run_suite(cases)
    report = build_report(results)

    previous = None
    if args.baseline and args.baseline.exists():
        previous = json.loads(args.baseline.read_text(encoding="utf-8"))
    regression = compare_runs(report, previous, results)

    args.out.mkdir(parents=True, exist_ok=True)
    payload = report.to_dict()
    payload["regression"] = regression.to_dict()
    (args.out / "report.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8"
    )
    (args.out / "report.html").write_text(render_html(report, regression), encoding="utf-8")

    print(render_json(report))
    print(
        f"\n{report.passed}/{report.total} passed "
        f"({report.pass_rate * 100:.1f}%), "
        f"{report.safety_failures} safety failure(s)"
    )

    safety_critical_failed = any(r.safety_critical and not r.passed for r in results)
    if regression.has_safety_regression or report.safety_failures or safety_critical_failed:
        print("SAFETY GATE FAILED", file=sys.stderr)
        return EXIT_SAFETY
    if report.failed:
        return EXIT_FAILURES
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
