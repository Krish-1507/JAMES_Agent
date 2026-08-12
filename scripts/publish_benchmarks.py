"""Publish the latest GAIA run to docs/BENCHMARKS.md.

Usage:
    python scripts/publish_benchmarks.py <report.json> [<report.json> ...] [-o docs/BENCHMARKS.md]

Each report is a gaia_report_*.json produced by `james --eval gaia`. A row is
appended to the results table (replacing the placeholder row if still present).
Used by the CI `Eval` workflow; safe to run locally too.

Provider/model are taken from the environment so the row records what actually
served the run (the harness report itself is model-agnostic).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

_ROWS_RE = re.compile(r"^\| \((no runs yet|\d{4}-\d{2}-\d{2})\) ", re.MULTILINE)


def _cell(value: str) -> str:
    return value.replace("|", "\\|").strip()


def _rows_for(report: dict, provider: str, model: str, command: str) -> list[str]:
    date = report.get("timestamp", "")[:10]
    levels = report.get("by_level", {})
    row = "| {date} | {model} | {split} | {tasks} | {rate} | {l1} | {l2} | {l3} | {cmd} |".format(
        date=_cell(date),
        model=_cell(f"{provider} / {model}" if provider else model or "unknown"),
        split="validation",
        tasks=report.get("total", 0),
        rate=f"{report.get('pass_rate', 0.0):.1%}",
        l1=levels.get("1", {}).get("pass_rate", "—"),
        l2=levels.get("2", {}).get("pass_rate", "—"),
        l3=levels.get("3", {}).get("pass_rate", "—"),
        cmd=_cell(command),
    )
    return [row]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reports", nargs="+", help="Path(s) to gaia_report_*.json")
    parser.add_argument("-o", "--output", default="docs/BENCHMARKS.md")
    args = parser.parse_args(argv)

    provider = os.environ.get("LLM_PROVIDER", "").lower()
    model = os.environ.get("LLM_MODEL", "")
    command = os.environ.get("EVAL_COMMAND", "python -m james --eval gaia")

    rows: list[str] = []
    for path in args.reports:
        report = json.loads(Path(path).read_text(encoding="utf-8"))
        rows.extend(_rows_for(report, provider, model, command))

    out = Path(args.output)
    text = out.read_text(encoding="utf-8")

    # Drop the "(no runs yet)" placeholder and any rows for the same dates,
    # then insert the new rows right after the table header.
    kept = [line for line in text.splitlines() if not _ROWS_RE.match(line)]
    header_at = next(
        i for i, line in enumerate(kept) if line.startswith("| Date | Provider / model |")
    )
    kept[header_at + 1 : header_at + 1] = rows
    out.write_text("\n".join(kept) + "\n", encoding="utf-8")
    print(f"[+] Published {len(rows)} row(s) to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
