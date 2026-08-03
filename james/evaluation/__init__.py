"""Evaluation and benchmarking framework for JAMES.

Provides tools to measure agent performance on tasks, track success rates,
and generate reports. Run with:  python -m james --eval
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from ..config import settings


@dataclass
class TaskResult:
    task_id: str
    task_description: str
    success: bool
    tool_calls: int
    iterations: int
    duration_seconds: float
    error: str | None = None
    timestamp: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class BenchmarkSuite:
    name: str
    tasks: list[dict[str, Any]]
    results: list[TaskResult] = field(default_factory=list)


class Evaluator:
    def __init__(self, output_dir: Path | None = None):
        self.output_dir = output_dir or settings.assistant.workspace_dir / "evaluations"
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._results: list[TaskResult] = []

    def run_task(
        self,
        description: str,
        agent_run_fn,
        max_iterations: int = 20,
        timeout: int = 120,
    ) -> TaskResult:
        task_id = f"eval_{int(time.time() * 1000)}"
        start = time.time()
        try:
            result, _ = agent_run_fn(description, max_iterations=max_iterations)
            duration = time.time() - start
            success = bool(
                result and not result.startswith("Error") and not result.startswith("I reached")
            )
            tool_calls = 0
            iterations = 0
            return TaskResult(
                task_id=task_id,
                task_description=description,
                success=success,
                tool_calls=tool_calls,
                iterations=iterations,
                duration_seconds=round(duration, 2),
                timestamp=datetime.now().isoformat(timespec="seconds"),
            )
        except Exception as exc:
            duration = time.time() - start
            return TaskResult(
                task_id=task_id,
                task_description=description,
                success=False,
                tool_calls=0,
                iterations=0,
                duration_seconds=round(duration, 2),
                error=str(exc),
                timestamp=datetime.now().isoformat(timespec="seconds"),
            )

    def run_suite(self, suite: BenchmarkSuite, agent_run_fn) -> list[TaskResult]:
        self._results = []
        for task in suite.tasks:
            result = self.run_task(task.get("description", ""), agent_run_fn)
            result.metadata = task.get("metadata", {})
            self._results.append(result)
        self._save_results(suite)
        return self._results

    def _save_results(self, suite: BenchmarkSuite) -> None:
        path = self.output_dir / f"{suite.name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        data = {
            "suite": suite.name,
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "total_tasks": len(suite.tasks),
            "passed": sum(1 for r in self._results if r.success),
            "failed": sum(1 for r in self._results if not r.success),
            "results": [asdict(r) for r in self._results],
        }
        path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")

    def summary(self) -> dict[str, Any]:
        if not self._results:
            return {"total": 0, "passed": 0, "failed": 0, "pass_rate": 0.0}  # nosec B105 - float literal, not a credential
        passed = sum(1 for r in self._results if r.success)
        total = len(self._results)
        return {
            "total": total,
            "passed": passed,
            "failed": total - passed,
            "pass_rate": round(passed / total, 2) if total > 0 else 0.0,
            "avg_duration": round(sum(r.duration_seconds for r in self._results) / total, 2),
        }


def run_benchmark(
    name: str,
    tasks: list[dict[str, Any]],
    agent_run_fn,
) -> dict[str, Any]:
    suite = BenchmarkSuite(name=name, tasks=tasks)
    evaluator = Evaluator()
    evaluator.run_suite(suite, agent_run_fn)
    return evaluator.summary()
