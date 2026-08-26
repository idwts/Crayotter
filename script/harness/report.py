from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path

from .models import HarnessReport


def write_json_report(report: HarnessReport, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    return path


def write_junit_report(report: HarnessReport, path: Path) -> Path:
    suite = ET.Element(
        "testsuite",
        name=f"crayotter-harness:{report.scenario_id}",
        tests=str(max(1, len(report.findings))),
        failures=str(
            sum(
                1
                for item in report.findings
                if not item.passed and item.severity == "error"
            )
        ),
        time=f"{report.wall_seconds:.3f}",
    )
    if not report.findings:
        ET.SubElement(
            suite, "testcase", name="scenario", time=f"{report.wall_seconds:.3f}"
        )
    for finding in report.findings:
        case = ET.SubElement(suite, "testcase", name=f"{finding.oracle}:{finding.code}")
        if not finding.passed and finding.severity == "error":
            failure = ET.SubElement(case, "failure", message=finding.message)
            failure.text = json.dumps(
                {"expected": finding.expected, "actual": finding.actual},
                ensure_ascii=False,
                default=str,
            )
        elif not finding.passed:
            ET.SubElement(case, "system-out").text = finding.message
    path.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(suite).write(path, encoding="utf-8", xml_declaration=True)
    return path
