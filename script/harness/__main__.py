from __future__ import annotations

import argparse
from pathlib import Path

from .loader import load_scenario
from .production import execute_production_scenario
from .report import write_json_report, write_junit_report
from .runner import HarnessRunner


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate a Crayotter reliability scenario."
    )
    parser.add_argument("scenario", type=Path)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--incident", type=Path, help="app_state/jobs/<job_id> directory")
    mode.add_argument(
        "--run", action="store_true", help="launch the normal Crayotter worker"
    )
    parser.add_argument("--output", type=Path, default=Path("harness-report.json"))
    parser.add_argument("--junit", type=Path)
    args = parser.parse_args()
    spec = load_scenario(args.scenario)
    runner = HarnessRunner(root=args.output.parent / ".harness-runtime")
    if args.incident is not None:
        outcome = runner.incident_outcome(args.incident)
        report = runner.run(spec, lambda _spec, _context: outcome)
    else:
        report = runner.run(spec, execute_production_scenario)
    write_json_report(report, args.output)
    if args.junit:
        write_junit_report(report, args.junit)
    print(f"scenario={report.scenario_id} passed={report.passed} report={args.output}")
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
