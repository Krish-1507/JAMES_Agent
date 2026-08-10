"""GAIA benchmark harness: dataset loading, answer matching, and scoring.

GAIA ("General AI Assistants", https://arxiv.org/abs/2311.12983) is a suite of
466 real-world assistant questions across three difficulty levels. The public
validation split (166 tasks with answers) is used for development; the test
split is private and scored on the Hugging Face leaderboard.

Run with:  james --eval gaia [--eval-dir DIR] [--eval-limit N] [--download-gaia]
"""

from __future__ import annotations

import json
import os
import re
import shutil
import string
import subprocess  # nosec B404 - required to run the isolated eval worker
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from ..config import settings

# Validation (public answers) and test (private answers) splits on HF.
GAIA_HF_BASE = "https://huggingface.co/datasets/gaia-benchmark/GAIA/resolve/main"

# Used when the caller does not pass a system prompt. GAIA is scored with
# quasi-exact match: extra words, units, or reasoning in the final answer
# fail the task even when the content is correct.
GAIA_SYSTEM_PROMPT = (
    "You are a meticulous assistant evaluated on exact answers. Use the "
    "available tools (read files, unzip archives, run calculations, extract "
    "text from documents and audio, describe images) to gather and verify "
    "facts before answering. When you are confident, your FINAL reply must be "
    "ONLY the answer itself: a single number, name, phrase, or short sentence "
    "that matches the question's expected answer exactly. Do not include "
    "explanations, units (unless the question names them), quotes, or "
    "punctuation not part of the answer. If the question states units (e.g. "
    "\"thousands\", \"%\", \"km\"), give the answer IN those units: for "
    "\"17 thousand\" answer 17, not 17000. The FINAL LINE of your final "
    "message must be the bare answer and nothing else — never lead that line "
    "with words like \"The answer is\", never wrap it in quotes, backticks, "
    "markdown, or bold, and never put any text after it — so a grader can "
    "read the answer directly off the last line. Never emit inline "
    "tool-calling markup such as <|DSML|> or <search> in your reply, and "
    "never describe tool usage in the final answer. Plan then act: for a "
    "multi-step task, write a short numbered plan as plain text (PLAN: 1) ... "
    "2) ...) in the SAME message as your first tool call, then execute it. "
    "Never let intermediate results leak into the final answer."
)
VALIDATION_SUBSET = "2023/validation"

_ARTICLES_RE = re.compile(r"\b(a|an|the)\b")
_PUNCT_SET = set(string.punctuation)


@dataclass
class GaiaTask:
    task_id: str
    question: str
    level: int
    answer: str
    file_name: str = ""
    file_path: Path | None = None


# ---------------------------------------------------------------------------
# Answer matching (mirrors the official GAIA evaluation script)
# ---------------------------------------------------------------------------


def normalize_number_str(number_str: str) -> str:
    if "%" in number_str:
        number = number_str.replace("%", "")
        try:
            return str(float(number) / 100)
        except ValueError:
            return number
    return number_str


def normalize_answer(s: str) -> str:
    """Lowercase, strip articles and punctuation, and collapse whitespace."""

    def remove_articles(text: str) -> str:
        return _ARTICLES_RE.sub(" ", text)

    def white_space_fix(text: str) -> str:
        return " ".join(text.split())

    def remove_punc(text: str) -> str:
        return "".join(ch for ch in text if ch not in _PUNCT_SET)

    return white_space_fix(remove_articles(remove_punc(s.lower())))


def is_float_close(prediction: str, target: str) -> bool:
    try:
        return abs(float(prediction) - float(target)) < 0.001
    except ValueError:
        return False


def is_number_close(prediction: str, target: str) -> bool:
    if prediction == target:
        return True
    try:
        return (
            abs(float(normalize_number_str(prediction)) - float(normalize_number_str(target)))
            < 0.001
        )
    except ValueError:
        return False


def is_string_close(prediction: str, target: str) -> bool:
    prediction = normalize_answer(prediction)
    target = normalize_answer(target)
    if prediction == target:
        return True
    # "Quasi-exact" match: answers that differ only by a typo or extra word
    # still count. 0.99 is the threshold used by the official GAIA scorer.
    return SequenceMatcher(None, prediction, target).ratio() >= 0.99


def is_correct(prediction: str, target: str) -> bool:
    """Quasi-exact match between a model reply and the GAIA ground truth."""
    prediction = (prediction or "").strip()
    target = (target or "").strip()
    if not prediction or not target:
        return False
    if is_string_close(prediction, target):
        return True
    if "%" in target:
        return is_number_close(prediction, target)
    try:
        float(target)
        return is_float_close(prediction, target)
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# Dataset loading / download
# ---------------------------------------------------------------------------


def load_gaia_metadata(eval_dir: Path) -> list[GaiaTask]:
    """Parse ``metadata.jsonl`` from a local GAIA validation folder.

    Accepts either the folder that directly contains ``metadata.jsonl`` or the
    parent folder of the standard ``2023/validation/`` layout. Newer GAIA
    distributions ship ``metadata.parquet``; if no JSONL exists, the parquet is
    converted in place (needs pyarrow).
    """
    meta = Path(eval_dir) / "metadata.jsonl"
    if not meta.exists():
        alt = Path(eval_dir) / VALIDATION_SUBSET / "metadata.jsonl"
        if alt.exists():
            eval_dir = alt.parent
            meta = alt
        else:
            parquet = Path(eval_dir) / VALIDATION_SUBSET / "metadata.parquet"
            if not parquet.exists():
                parquet = Path(eval_dir) / "metadata.parquet"
            if parquet.exists():
                _parquet_to_jsonl(parquet, parquet.with_suffix(".jsonl"))
                eval_dir, meta = parquet.parent, parquet.with_suffix(".jsonl")
            else:
                raise FileNotFoundError(
                    f"No metadata.jsonl or metadata.parquet found under {eval_dir}. "
                    "Run `james --eval gaia --download-gaia` or point --eval-dir at a GAIA folder."
                )

    tasks: list[GaiaTask] = []
    for line in meta.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        record = json.loads(line)
        file_name = _attachment_name(record)
        file_path = None
        if file_name:
            candidate = eval_dir / file_name
            if candidate.exists():
                file_path = candidate
        level_raw = record.get("Level", "1")
        tasks.append(
            GaiaTask(
                task_id=str(record.get("task_id") or ""),
                question=str(record.get("Question") or "").strip(),
                level=_parse_level(level_raw),
                answer=str(record.get("Final answer") or "").strip(),
                file_name=file_name,
                file_path=file_path,
            )
        )
    return tasks


def _parse_level(raw) -> int:
    text = str(raw).strip()
    match = re.search(r"\d", text)
    level = int(match.group(0)) if match else 1
    return level if 1 <= level <= 3 else 1


def _hf_headers() -> dict[str, str]:
    """Auth headers for the gated GAIA dataset. Read-only Hugging Face token
    via ``HF_TOKEN`` (or ``HUGGINGFACE_TOKEN``); a token is required since the
    dataset is gated — accept the terms on the dataset page once, then use
    ``huggingface-cli login`` or export ``HF_TOKEN``."""
    token = os.environ.get("HF_TOKEN") or os.environ.get("HUGGINGFACE_TOKEN") or ""
    return {"Authorization": f"Bearer {token}"} if token else {}


def _attachment_name(record: dict) -> str:
    """Attachment id for a GAIA record. Newer parquet splits keep the
    repo-relative ``file_path``; both old JSONL and new parquet carry the bare
    file name in ``file_name``. We only ever need the basename."""
    name = str(record.get("file_name") or "").strip()
    if not name:
        name = str(record.get("file_path") or "").strip()
    if not name:
        return ""
    name = name.replace("\\", "/")
    return Path(name).name


def _parquet_to_jsonl(parquet_path: Path, jsonl_path: Path, limit: int = 0) -> list[dict]:
    """Convert GAIA ``metadata.parquet`` to a JSONL file (requires pyarrow).

    GAIA switched to Parquet-backed splits in October 2025; the JSONL view is
    kept as the harness's canonical input so old and new distributions use one
    code path.
    """
    try:
        import pyarrow.parquet as pq
    except ImportError as exc:  # pragma: no cover - depends on extras
        raise RuntimeError(
            "GAIA metadata is now distributed as Parquet. Install the benchmark "
            'dependencies first: pip install -e ".[docs]" (adds pyarrow).'
        ) from exc
    rows = pq.read_table(parquet_path).to_pylist()
    if limit:
        rows = rows[:limit]
    with jsonl_path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, default=str) + "\n")
    return rows


def _fetch_metadata(out_dir: Path, subset: str) -> list[dict]:
    """Fetch the metadata split from HF as JSONL records.

    Tries the legacy ``metadata.jsonl`` first, then falls back to the current
    ``metadata.parquet`` (converted to JSONL on disk).
    """
    import requests

    def _get(name: str) -> requests.Response:
        return requests.get(
            f"{GAIA_HF_BASE}/{subset}/{name}",
            timeout=120,
            headers=_hf_headers(),
        )

    resp = _get("metadata.jsonl")
    if resp.status_code == 200:
        return [json.loads(line) for line in resp.text.splitlines() if line.strip()]
    if resp.status_code in (401, 403):
        raise PermissionError(
            "GAIA is a gated dataset. Accept the terms at "
            "https://huggingface.co/datasets/gaia-benchmark/GAIA (Settings → "
            "Access token → Read), then set HF_TOKEN (or HUGGINGFACE_TOKEN)."
        )
    parquet_resp = _get("metadata.parquet")
    if parquet_resp.status_code != 200:
        parquet_resp.raise_for_status()
    pq_path = out_dir / "metadata.parquet"
    pq_path.write_bytes(parquet_resp.content)
    return _parquet_to_jsonl(pq_path, out_dir / "metadata.jsonl")


def download_gaia(dest_dir: Path, subset: str = VALIDATION_SUBSET, limit: int = 0) -> Path:
    """Download the public GAIA validation split (metadata + attachments)."""
    import requests

    out_dir = Path(dest_dir) / subset
    out_dir.mkdir(parents=True, exist_ok=True)

    records = _fetch_metadata(out_dir, subset)
    if limit:
        records = records[:limit]

    downloaded = 0
    for record in records:
        file_name = _attachment_name(record)
        if not file_name:
            continue
        target = out_dir / file_name
        if target.exists() and target.stat().st_size > 0:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        file_resp = requests.get(
            f"{GAIA_HF_BASE}/{subset}/{file_name}",
            timeout=120,
            stream=True,
            headers=_hf_headers(),
        )
        if file_resp.status_code != 200:
            continue
        target.write_bytes(file_resp.content)
        downloaded += 1

    (out_dir / "metadata.jsonl").write_text(
        "\n".join(json.dumps(r, default=str) for r in records) + "\n",
        encoding="utf-8",
    )
    return out_dir


# ---------------------------------------------------------------------------
# Running tasks
# ---------------------------------------------------------------------------


def _run_task_subprocess(
    task: GaiaTask,
    scratch_dir: Path,
    max_iterations: int,
    task_timeout: int,
    system_prompt: str | None,
) -> tuple[dict[str, int], str]:
    """Run one task in a fresh interpreter with a hard timeout.

    The child inherits the parent's environment (API keys, .env), builds its
    own provider, and prints a JSON result. A hung LLM call cannot stall the
    suite — the subprocess is killed at ``task_timeout``.
    """
    payload = {
        "question": task.question,
        "file_path": str(task.file_path) if task.file_path else "",
        "scratch_dir": str(scratch_dir),
        "max_iterations": max_iterations,
        "system_prompt": system_prompt,
    }
    # The child runs from the scratch dir, so `james` would not be importable
    # from there in a repo checkout. Always put the package's parent on the
    # child's PYTHONPATH (a no-op when james is pip-installed).
    import james as _james

    pkg_parent = str(Path(_james.__file__).resolve().parent.parent)
    env = dict(os.environ)
    env["PYTHONPATH"] = pkg_parent + (
        os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else ""
    )
    try:
        proc = subprocess.run(  # nosec B603 - argv list, no shell; fixed worker entry point
            [sys.executable, "-m", "james.evaluation.worker", json.dumps(payload)],
            cwd=str(scratch_dir),
            capture_output=True,
            text=True,
            timeout=max(1, task_timeout),
            check=False,
            env=env,
        )
    except subprocess.TimeoutExpired as exc:
        raw = exc.stderr or b""
        tail = (
            raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
        ).strip().splitlines()
        detail = tail[-1] if tail else "no stderr"
        return "", {
            "tool_calls": 0,
            "iterations": 0,
            "error": f"worker timeout ({detail})",
        }

    output = (proc.stdout or "").strip()
    if not output:
        detail = (proc.stderr or "").strip().splitlines()
        return "", {
            "tool_calls": 0,
            "iterations": 0,
            "error": f"worker rc={proc.returncode}: {detail[-1] if detail else 'no output'}",
        }
    try:
        parsed = json.loads(output)
    except json.JSONDecodeError:
        return "", {"tool_calls": 0, "iterations": 0, "error": "worker produced invalid JSON"}
    if not parsed.get("ok", True):
        return str(parsed.get("reply", "")), {
            "tool_calls": 0,
            "iterations": 0,
            "error": str(parsed.get("error", "worker error")),
        }
    return str(parsed.get("reply", "")), {
        "tool_calls": int(parsed.get("tool_calls", 0)),
        "iterations": int(parsed.get("iterations", 0)),
    }


def _copy_attachment(task: GaiaTask, scratch_dir: Path) -> Path | None:
    """Stage the task's attachment into the scratch dir; returns the staged path."""
    if not task.file_path or not task.file_path.exists():
        return None
    staged = scratch_dir / task.file_path.name
    if staged.resolve() != task.file_path.resolve():
        shutil.copy2(task.file_path, staged)
    return staged


def run_gaia_suite(
    tasks: list[GaiaTask],
    *,
    agent_fn: Callable[[GaiaTask, Path], tuple[dict, str]] | None = None,
    max_iterations: int = 20,
    task_timeout: int = 300,
    output_dir: Path | None = None,
    system_prompt: str | None = None,
) -> dict[str, Any]:
    """Run a GAIA task list and return a level-stratified report.

    ``agent_fn(task, scratch_dir) -> (stats, reply)`` replaces the default
    subprocess runner (used by tests and offline smoke runs).
    """
    if system_prompt is None:
        system_prompt = GAIA_SYSTEM_PROMPT
    from . import BenchmarkSuite, Evaluator, TaskResult

    out = Path(output_dir) if output_dir else settings.assistant.workspace_dir / "evaluations"
    out.mkdir(parents=True, exist_ok=True)
    runs_dir = out / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)

    suite = BenchmarkSuite(name=f"gaia_{time.strftime('%Y%m%d_%H%M%S')}", tasks=[])
    suite.tasks = [
        {
            "description": task.question,
            "metadata": {
                "task_id": task.task_id,
                "level": task.level,
                "answer": task.answer,
                "file_name": task.file_name,
            },
        }
        for task in tasks
    ]
    evaluator = Evaluator(output_dir=out)

    results: list[TaskResult] = []
    for task, entry in zip(tasks, suite.tasks, strict=True):
        scratch = runs_dir / (task.task_id or f"task_{len(results)}")
        scratch.mkdir(parents=True, exist_ok=True)
        staged = _copy_attachment(task, scratch)
        staged_task = GaiaTask(
            task_id=task.task_id,
            question=task.question,
            level=task.level,
            answer=task.answer,
            file_name=task.file_name,
            file_path=staged,
        )
        if agent_fn is None:

            def _run_one(
                description: str, *, _staged=staged_task, _scratch=scratch, **kw
            ) -> tuple[str, dict]:
                return _run_task_subprocess(
                    _staged, _scratch, max_iterations, task_timeout, system_prompt
                )
        else:

            def _run_one(
                description: str, *, _staged=staged_task, _scratch=scratch, **kw
            ) -> tuple[str, dict]:
                return agent_fn(_staged, _scratch)

        tr = evaluator.run_task(
            entry["description"],
            _run_one,
            max_iterations=max_iterations,
            timeout=task_timeout,
            metadata=entry["metadata"],
        )
        tr.success = is_correct(tr.output, entry["metadata"]["answer"])
        results.append(tr)
        # Persist after every task so a crash or kill never loses the whole
        # run — with tool-using agents a suite can run for tens of minutes.
        suite.results = results
        evaluator._save_results(suite)

    suite.results = results
    evaluator._save_results(suite)
    return _build_report(results, out)


def _build_report(results, out: Path) -> dict[str, Any]:
    total = len(results)
    passed = sum(1 for r in results if r.success)
    by_level: dict[str, dict[str, Any]] = {}
    for level in (1, 2, 3):
        subset = [r for r in results if (r.metadata or {}).get("level") == level]
        if not subset:
            continue
        sub_passed = sum(1 for r in subset if r.success)
        by_level[str(level)] = {
            "total": len(subset),
            "passed": sub_passed,
            "pass_rate": round(sub_passed / len(subset), 4),
        }

    tool_calls = [r.tool_calls for r in results] or [0]
    iterations = [r.iterations for r in results] or [0]
    durations = [r.duration_seconds for r in results] or [0]

    report = {
        "suite": "gaia_validation",
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "total": total,
        "passed": passed,
        "pass_rate": round(passed / total, 4) if total else 0.0,
        "by_level": by_level,
        "avg_tool_calls": round(sum(tool_calls) / len(tool_calls), 2),
        "avg_iterations": round(sum(iterations) / len(iterations), 2),
        "avg_duration_seconds": round(sum(durations) / len(durations), 2),
        "results": [
            {
                "task_id": r.task_id,
                "level": (r.metadata or {}).get("level"),
                "success": r.success,
                "expected": (r.metadata or {}).get("answer"),
                "output": r.output,
                "tool_calls": r.tool_calls,
                "iterations": r.iterations,
                "duration_seconds": r.duration_seconds,
                "error": r.error,
            }
            for r in results
        ],
    }
    path = out / f"gaia_report_{time.strftime('%Y%m%d_%H%M%S')}.json"
    path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    return report
