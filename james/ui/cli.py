"""Professional terminal chat interface for JAMES.

Renders a clean, OpenCode-style chat: a status header, colour-coded message
panels for the user and JAMES, a spinner while the model is thinking, dim
inline tool status, and a styled input prompt. Depends only on ``rich`` (a
core dependency) so it always works in ``--text`` mode.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager, suppress
from datetime import datetime

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table
from rich.text import Text

_DIM = "dim"
_ACCENT = "cyan"

_LOGO = r"""
     _   _    __  __ _____ ____
    | | / \  |  \/  | ____/ ___|
 _  | |/ _ \ | |\/| |  _| \___ \
| |_| / ___ \| |  | | |___ ___) |
 \___/_/   \_\_|  |_|_____|____/
"""


class JamesCLI:
    """Renders the interactive ``james --text`` chat session."""

    def __init__(self) -> None:
        # Rich falls back to the process code page on legacy Windows consoles,
        # which cannot encode the decorative box/marker glyphs this UI uses
        # (box-drawing, check marks, and the prompt arrow). Reconfigure the
        # streams to UTF-8 so the panels always render.
        def _reconfigure(stream) -> None:
            configure = getattr(stream, "reconfigure", None)
            if (
                configure is not None
                and stream.encoding
                and stream.encoding.lower() not in ("utf-8", "utf8")
            ):
                with suppress(Exception):
                    configure(encoding="utf-8")

        try:
            import sys

            _reconfigure(sys.stdout)
            _reconfigure(sys.stderr)
        except Exception:  # nosec B110 - best-effort UTF-8 stream setup
            pass
        self.console = Console()
        self._printed_header = False

    # ------------------------------------------------------------------ header
    def print_logo(self, version: str) -> None:
        """Render the ASCII JAMES logo with a one-line caption underneath."""
        logo = Text.assemble(
            (_LOGO, "bold cyan"),
            ("      JAMES — Just A Modular Executive System", "bold white"),
            (f"      v{version}\n", _DIM),
        )
        self.console.print(logo)

    def print_header(self, *, provider: str, model: str, session: str, version: str) -> None:
        """Draw the persistent status bar shown above the chat stream."""
        if self._printed_header:
            return
        self._printed_header = True

        status = Table.grid(padding=(0, 1))
        status.add_column(justify="left")
        status.add_column(justify="right")

        brand = Text.assemble(
            ("JAMES", "bold white"),
            ("  ·  ", _DIM),
            (version, _DIM),
        )
        meta = Text.assemble(
            ("provider  ", "bold"),
            (provider, _ACCENT),
            ("    model  ", "bold"),
            (model, _ACCENT),
            ("    session  ", "bold"),
            (session, _ACCENT),
        )
        status.add_row(brand, meta)

        self.console.print(Panel(status, box=box.ROUNDED, border_style=_ACCENT, padding=(0, 1)))

    # ------------------------------------------------------------------ prompt
    def read_prompt(self, user_name: str) -> str:
        """Read one line from the user through the composer prompt."""
        user_tag = Text(f" {user_name}  ", style="black on green")
        arrow = Text(" ❯ ", style="bold")  # noqa: RUF001
        return Prompt.ask(Text.assemble(user_tag, arrow), default="")

    # ----------------------------------------------------------------- message
    def print_user(self, text: str) -> None:
        title = Text(" You ", style="bold")
        self.console.print(
            Panel(
                text,
                title=title,
                border_style="green",
                box=box.ROUNDED,
                padding=(0, 1),
            )
        )

    def print_assistant(self, name: str, text: str) -> None:
        title = Text(f" {name} ", style="bold")
        self.console.print(
            Panel(
                text,
                title=title,
                subtitle=self._stamp(),
                border_style=_ACCENT,
                box=box.ROUNDED,
                padding=(0, 1),
            )
        )

    def print_welcome(self, name: str, user_name: str) -> None:
        message = (
            f"Good to see you, {user_name}. I'm {name} — your modular "
            "executive assistant.\n\n"
            "I can work with files, documents, your browser and desktop, "
            "remember things, schedule tasks, and more. Ask away — or use a "
            "slash command."
        )
        self.print_assistant(name, message)

    def print_hint(self, text: str) -> None:
        self.console.print(Text(text, style=_DIM))

    def print_session_message(self, text: str) -> None:
        self.console.print(Text(f"·  {text}", style=_ACCENT))

    def thinking(self) -> Iterator[None]:
        """Context manager wrapping a think while showing a live spinner."""

        @contextmanager
        def _spin() -> Iterator[None]:
            try:
                with self.console.status("JAMES is thinking…", spinner="dots", spinner_style=_DIM):
                    yield
            except TypeError:  # rich <14 used ``style=`` instead of ``spinner_style=``
                with self.console.status("JAMES is thinking…", spinner="dots", style=_DIM):
                    yield

        return _spin()

    # ------------------------------------------------------------------- tools
    def print_tool_start(self, name: str, args: str) -> None:
        self.console.print(Text(f"   │ {name}({args})", style=_DIM))

    def print_tool_done(self, name: str, ok: bool, result: str) -> None:
        tag = "✓" if ok else "✗"
        style = "green" if ok else "red"
        line = Text.assemble(
            (f"   {tag} ", style),
            (f"{name}", "bold"),
            ("  ", _DIM),
            (self._one_line(result), _DIM),
        )
        self.console.print(line)

    # ------------------------------------------------------------------- util
    def _stamp(self) -> Text:
        return Text(datetime.now().strftime("%H:%M"), style=_DIM)

    @staticmethod
    def _one_line(text: str) -> str:
        collapsed = " ".join((text or "").split())
        return collapsed[:120] + "…" if len(collapsed) > 120 else collapsed


def create_cli() -> JamesCLI:
    """Instantiate a CLI renderer, using the configured assistant name."""
    return JamesCLI()
