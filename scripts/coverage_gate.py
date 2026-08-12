#!/usr/bin/env python3
"""Coverage gate for security-critical modules.

Runs the full test suite with coverage measurement for the modules that
implement the safety boundary (offline egress guard, subprocess isolation,
secret handling, script forging, tool permissions, web access, MCP client
sanitization) and fails the build if any of them drops below the required
percentage.

Usage:
    python scripts/coverage_gate.py [--threshold 80] [--module james.core.guard ...]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_MODULES = [
    "james.core.guard",
    "james.core.isolation",
    "james.core.secrets",
    "james.tools.forge_tools",
    "james.tools.registry",
    "james.tools.web_tools",
    "james.tools.mcp_tools",
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--threshold", type=float, default=80.0)
    parser.add_argument("--module", action="append", default=[], dest="modules")
    args = parser.parse_args()

    modules = args.modules or DEFAULT_MODULES
    threshold = args.threshold

    cov_args = []
    for module in modules:
        cov_args += ["--cov", module]
    cov_args += ["--cov-report", "json"]

    with tempfile.TemporaryDirectory() as tmp:
        report = Path(tmp) / "cov.json"
        cmd = [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "--timeout=240",
            *cov_args,
            "--cov-report",
            f"json:{report}",
        ]
        proc = subprocess.run(cmd, cwd=REPO_ROOT)
        if not report.exists():
            print("coverage report was not produced", file=sys.stderr)
            return proc.returncode or 1

        data = json.loads(report.read_text(encoding="utf-8"))
        totals = data.get("totals", {})
        failed: list[tuple[str, float, float]] = []
        for module in modules:
            entry = data.get("files", {}).get(f"{module}.py")
            if entry is None:
                print(f"no coverage data for {module}", file=sys.stderr)
                failed.append((module, 0.0, threshold))
                continue
            percent = entry["summary"]["percent_covered"]
            print(f"{module:45s} {percent:6.1f}%")
            if percent < threshold:
                failed.append((module, percent, threshold))

        print(f"\noverall coverage: {totals.get('percent_covered', 0):.1f}%")
        if failed:
            print("\nFAILED coverage gate (need >= {:.0f}%):".format(threshold))
            for module, percent, need in failed:
                print(f"  {module}: {percent:.1f}% < {need:.0f}%")
            return 1
        print("coverage gate passed")
        return 0


if __name__ == "__main__":
    sys.exit(main())
