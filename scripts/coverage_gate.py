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

    # The two tail files run in a second, fresh interpreter (see ci.yml):
    # a long-lived pytest process on the ubuntu runner degrades once ~90% of
    # the suite has executed, so the last files get their own process. The
    # coverage data is merged via COVERAGE_FILE + --cov-append.
    TAIL_FILES = [
        "tests/test_phase5_server_ui.py",
        "tests/test_security_guard_isolation.py",
    ]

    with tempfile.TemporaryDirectory() as tmp:
        report = Path(tmp) / "cov.json"
        env = {**os.environ, "COVERAGE_FILE": str(Path(tmp) / ".coverage")}
        base = [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "--timeout=240",
            *cov_args,
            "--cov-report",
            f"json:{report}",
        ]
        main_cmd = base + [f"--ignore={f}" for f in TAIL_FILES]
        tail_cmd = base + ["--cov-append", *TAIL_FILES]
        for label, cmd in (("main suite", main_cmd), ("tail files", tail_cmd)):
            proc = subprocess.run(cmd, cwd=REPO_ROOT, env=env)
            if proc.returncode != 0:
                print(f"pytest ({label}) failed", file=sys.stderr)
                return proc.returncode
        if not report.exists():
            print("coverage report was not produced", file=sys.stderr)
            return 1

        data = json.loads(report.read_text(encoding="utf-8"))
        totals = data.get("totals", {})
        files = {k.replace("\\", "/"): v for k, v in data.get("files", {}).items()}
        failed: list[tuple[str, float, float]] = []
        for module in modules:
            entry = files.get(f"{module}.py")
            if entry is None:
                rel = "/".join(module.split(".")) + ".py"
                entry = files.get(rel)
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
            print(f"\nFAILED coverage gate (need >= {threshold:.0f}%):")
            for module, percent, need in failed:
                print(f"  {module}: {percent:.1f}% < {need:.0f}%")
            return 1
        print("coverage gate passed")
        return 0


if __name__ == "__main__":
    sys.exit(main())
