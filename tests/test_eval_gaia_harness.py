"""Tests for the GAIA benchmark harness and the Level-1 reading/compute tools.

Phase 0 of the master plan: measurable agent capability. These tests run
offline (no API keys, no network) — they exercise the scoring logic, dataset
loading, the evaluator's metrics, and the tool sandboxes.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from james.evaluation import Evaluator
from james.evaluation.gaia import (
    GaiaTask,
    is_correct,
    is_float_close,
    is_number_close,
    load_gaia_metadata,
    normalize_answer,
    run_gaia_suite,
)
from james.tools.compute_tools import UnsafeExpression, calculate, evaluate_expression
from james.tools.file_tools import unzip_archive

# ---------------------------------------------------------------------------
# GAIA answer matching
# ---------------------------------------------------------------------------


def test_normalize_answer_lowercases_and_strips_articles_punctuation() -> None:
    assert normalize_answer("The answer is 42.") == normalize_answer("answer is 42")
    assert normalize_answer("  A quick, brown fox!  ") == normalize_answer("quick brown fox")
    assert normalize_answer("Paris, France") == normalize_answer("paris france")


def test_is_correct_exact_and_quasi_exact() -> None:
    assert is_correct("42", "42")
    assert is_correct("  42  ", "42")
    assert is_correct("Paris, France", "Paris, France!")  # punctuation-insensitive
    assert not is_correct("London", "Paris")
    # The official GAIA scorer is strict about extra words in the answer.
    assert not is_correct("The answer is 42.", "42")
    assert not is_correct("Paris", "Paris, France")  # extra word, not just punctuation


def test_is_correct_number_forms() -> None:
    assert is_correct("1,000", "1000")
    assert is_correct("0.5", "50%")  # percent target matched via number closeness
    assert is_correct("1234.5678", "1234.5678")
    assert is_float_close("1234.5677", "1234.5678")
    assert is_number_close("50%", "0.5")


def test_is_string_close_fuzzy_threshold() -> None:
    from james.evaluation.gaia import is_string_close

    assert is_string_close("a" * 100, "a" * 99 + "b")  # ratio 0.99
    assert not is_string_close("hello world", "goodbye world")


def test_is_correct_rejects_empties() -> None:
    assert not is_correct("", "42")
    assert not is_correct("42", "")


# ---------------------------------------------------------------------------
# Dataset loading
# ---------------------------------------------------------------------------


def _write_gaia_fixture(eval_dir: Path) -> None:
    eval_dir.mkdir(parents=True, exist_ok=True)
    (eval_dir / "notes.txt").write_text("hello", encoding="utf-8")
    rows = [
        {
            "task_id": "0-1-1",
            "Question": "What is the file size of the attachment?",
            "Level": "1",
            "Final answer": "12 bytes",
            "file_name": "notes.txt",
            "Annotator Metadata": {},
        },
        {
            "task_id": "1-2-1",
            "Question": "Which country is Paris in?",
            "Level": 2,
            "Final answer": "France",
            "file_name": "",
        },
    ]
    (eval_dir / "metadata.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8"
    )


def test_load_gaia_metadata_parses_fixture(tmp_path: Path) -> None:
    _write_gaia_fixture(tmp_path)
    tasks = load_gaia_metadata(tmp_path)
    assert len(tasks) == 2
    first = tasks[0]
    assert first.task_id == "0-1-1"
    assert first.level == 1
    assert first.answer == "12 bytes"
    assert first.file_path is not None and first.file_path.exists()
    assert tasks[1].level == 2
    assert tasks[1].file_path is None


def test_load_gaia_metadata_accepts_nested_validation_layout(tmp_path: Path) -> None:
    nested = tmp_path / "2023" / "validation"
    _write_gaia_fixture(nested)
    tasks = load_gaia_metadata(tmp_path)
    assert len(tasks) == 2


def test_load_gaia_metadata_missing_folder_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_gaia_metadata(tmp_path / "nope")


# ---------------------------------------------------------------------------
# Evaluator metrics
# ---------------------------------------------------------------------------


def test_evaluator_records_tool_calls_and_iterations() -> None:
    evaluator = Evaluator()

    def _agent(description: str, **kw):
        return ("finished the task", {"tool_calls": 7, "iterations": 4})

    result = evaluator.run_task("a task", _agent)
    assert result.success
    assert result.tool_calls == 7
    assert result.iterations == 4
    assert result.output == "finished the task"
    assert result.metadata == {}
    assert len(evaluator._results) == 1


def test_evaluator_accepts_plain_string_reply() -> None:
    evaluator = Evaluator()
    result = evaluator.run_task("a task", lambda desc, **kw: "plain reply")
    assert result.success
    assert result.tool_calls == 0
    assert result.output == "plain reply"


def test_evaluator_captures_failures() -> None:
    evaluator = Evaluator()

    def _boom(description: str, **kw):
        raise RuntimeError("kaboom")

    result = evaluator.run_task("a task", _boom)
    assert not result.success
    assert result.error == "kaboom"


# ---------------------------------------------------------------------------
# GAIA suite orchestration (stubbed agent, offline)
# ---------------------------------------------------------------------------


def test_run_gaia_suite_scores_and_stratifies_by_level(
    isolated_workspace: Path, tmp_path: Path
) -> None:
    tasks = [
        GaiaTask(task_id="a1", question="Q1", level=1, answer="42", file_name=""),
        GaiaTask(task_id="a2", question="Q2", level=1, answer="Paris", file_name=""),
        GaiaTask(task_id="b1", question="Q3", level=2, answer="7", file_name=""),
    ]

    def _fake_agent(task: GaiaTask, scratch: Path) -> tuple[dict, str]:
        answers = {"Q1": "42", "Q2": "wrong", "Q3": "7"}
        return (answers[task.question], {"tool_calls": 3, "iterations": 2})

    report = run_gaia_suite(tasks, agent_fn=_fake_agent, output_dir=tmp_path / "out")
    assert report["total"] == 3
    assert report["passed"] == 2
    assert report["by_level"]["1"] == {"total": 2, "passed": 1, "pass_rate": 0.5}
    assert report["by_level"]["2"] == {"total": 1, "passed": 1, "pass_rate": 1.0}
    assert report["avg_tool_calls"] == 3.0
    assert report["avg_iterations"] == 2.0
    assert (tmp_path / "out" / "runs" / "a1").is_dir()
    assert any((tmp_path / "out").glob("gaia_report_*.json"))


def test_run_gaia_suite_stages_attachment(isolated_workspace: Path, tmp_path: Path) -> None:
    attach = tmp_path / "data.csv"
    attach.write_text("a,b\n1,2\n", encoding="utf-8")
    tasks = [
        GaiaTask(
            task_id="c1", question="Q", level=1, answer="x", file_name="data.csv", file_path=attach
        )
    ]

    staged_paths: list[Path] = []

    def _fake_agent(task: GaiaTask, scratch: Path) -> tuple[dict, str]:
        staged_paths.append(task.file_path)
        return ("x", {"tool_calls": 1, "iterations": 1})

    run_gaia_suite(tasks, agent_fn=_fake_agent, output_dir=tmp_path / "out")
    assert len(staged_paths) == 1
    assert staged_paths[0] is not None
    assert staged_paths[0].name == "data.csv"
    assert staged_paths[0].read_text(encoding="utf-8") == "a,b\n1,2\n"


def test_worker_subprocess_imports_package_from_scratch_cwd(
    isolated_workspace: Path, tmp_path: Path, monkeypatch
) -> None:
    """The worker child runs with cwd=scratch_dir; the harness must propagate
    the package's parent via PYTHONPATH (regression: children died with
    'No module named james' before the fix)."""
    from james.evaluation import gaia as gaia_mod

    monkeypatch.setenv("LLM_PROVIDER", "custom")
    monkeypatch.setenv("CUSTOM_API_KEY", "x")
    monkeypatch.setenv("CUSTOM_BASE_URL", "http://127.0.0.1:9/v1")
    monkeypatch.setenv("LLM_MODEL", "x")

    task = GaiaTask(task_id="spawn1", question="Q", level=1, answer="42", file_name="")
    reply, stats = gaia_mod._run_task_subprocess(task, tmp_path, 20, 60, "prompt")
    assert reply == ""
    assert "No module named 'james'" not in stats.get("error", "")


def test_agent_headless_propagates_llm_errors(isolated_workspace: Path) -> None:
    """In headless mode (confirm_dangerous=False, e.g. the eval worker) an LLM
    API error must raise instead of blocking on an interactive retry prompt."""
    from james.core.agent import Agent
    from james.tools.registry import ToolRegistry

    class _Broken:
        def chat(self, *args, **kwargs):
            raise RuntimeError("boom")

    agent = Agent(_Broken(), ToolRegistry(discover_plugins=False), confirm_dangerous=False)
    with pytest.raises(RuntimeError, match="boom"):
        agent.run("hello")


# ---------------------------------------------------------------------------
# unzip_archive
# ---------------------------------------------------------------------------


def _make_zip(path: Path, entries: dict[str, str]) -> Path:
    with zipfile.ZipFile(str(path), "w") as zf:
        for name, content in entries.items():
            zf.writestr(name, content)
    return path


def test_unzip_archive_extracts_entries(isolated_workspace: Path) -> None:
    archive = _make_zip(
        isolated_workspace / "data.zip",
        {"folder/one.txt": "hello", "folder/two.txt": "world"},
    )
    result = unzip_archive.run(path=str(archive), destination=str(isolated_workspace / "out"))
    assert result.ok
    assert "2 entries" in result.output
    assert (isolated_workspace / "out" / "folder" / "one.txt").read_text() == "hello"


def test_unzip_archive_blocks_zip_slip(isolated_workspace: Path) -> None:
    archive = _make_zip(
        isolated_workspace / "evil.zip",
        {"../escape.txt": "gotcha"},
    )
    result = unzip_archive.run(path=str(archive), destination=str(isolated_workspace / "out"))
    assert not result.ok
    assert "Blocked unsafe archive entry" in result.output
    assert not (isolated_workspace.parent / "escape.txt").exists()


# ---------------------------------------------------------------------------
# calculate (sandboxed math)
# ---------------------------------------------------------------------------


def test_calculate_basic_arithmetic() -> None:
    assert evaluate_expression("2**10") == 1024
    assert evaluate_expression("(3+5)*7") == 56
    assert evaluate_expression("sqrt(144) + log2(8)") == 15
    assert evaluate_expression("sin(pi/2)") == pytest.approx(1.0)
    assert evaluate_expression("floor(3.7)") == 3
    assert evaluate_expression("min(4, 2, 9)") == 2


def test_calculate_tool_formats_output() -> None:
    result = calculate.run(expression="2**10")
    assert result.ok
    assert result.output == "1024"


def test_calculate_rejects_unsafe_constructs() -> None:
    for expr in (
        "__import__('os').system('x')",
        "os.system('x')",
        "open('/etc/passwd')",
        "[i for i in range(10)]",
        "lambda x: x",
        "(1).__class__",
        "1 if 1 else 2",
        "a + b",
        "'x' * 5",
        "getattr(1, 'real')",
    ):
        with pytest.raises(UnsafeExpression):
            evaluate_expression(expr)
        result = calculate.run(expression=expr)
        assert not result.ok


# ---------------------------------------------------------------------------
# Level-1 reading tools (offline paths only)
# ---------------------------------------------------------------------------


def test_read_document_csv(isolated_workspace: Path) -> None:
    from james.tools.reading_tools import read_document

    csv_file = isolated_workspace / "data.csv"
    csv_file.write_text("name,age\nAlice,30\nBob,25\n", encoding="utf-8")
    result = read_document.run(path=str(csv_file))
    assert result.ok
    assert "Alice" in result.output
    assert "30" in result.output


def test_read_pdf_missing_dependency_message(isolated_workspace: Path) -> None:
    from james.tools.reading_tools import read_pdf

    missing = isolated_workspace / "nonexistent.pdf"
    result = read_pdf.run(path=str(missing))
    assert not result.ok
    assert "not found" in result.output.lower()


def test_describe_image_missing_file(isolated_workspace: Path) -> None:
    from james.tools.reading_tools import describe_image

    result = describe_image.run(path=str(isolated_workspace / "nope.png"))
    assert not result.ok


@pytest.mark.skipif(
    not __import__("importlib.util", fromlist=["find_spec"]).find_spec("pypdf"),
    reason="pypdf not installed",
)
def test_read_pdf_extracts_text(isolated_workspace: Path) -> None:
    from james.tools.reading_tools import read_pdf

    pdf_file = isolated_workspace / "doc.pdf"
    try:
        from reportlab.pdfgen import canvas
    except ImportError:
        pytest.skip("reportlab not installed")
    c = canvas.Canvas(str(pdf_file))
    c.drawString(100, 750, "GAIA validation fixture")
    c.save()

    result = read_pdf.run(path=str(pdf_file))
    assert result.ok
    assert "GAIA validation fixture" in result.output


def test_read_document_txt_fallback(isolated_workspace: Path) -> None:
    from james.tools.reading_tools import read_document

    note = isolated_workspace / "note.txt"
    note.write_text("plain text note", encoding="utf-8")
    result = read_document.run(path=str(note))
    assert result.ok
    assert "plain text note" in result.output
