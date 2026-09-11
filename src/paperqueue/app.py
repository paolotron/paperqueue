"""Textual TUI for the paper stack."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.theme import Theme
from textual.widgets import DataTable, Footer, Header, Input, Static

from paperqueue import links, opener
from paperqueue.config import load_server_config, load_theme, save_theme
from paperqueue.remote import RemoteError, RemoteStack
from paperqueue.store import Paper, Stack

POLL_SECONDS = 5

# The same De Stijl palette the web client uses, so both ports look like one app.
THEMES = [
    Theme(
        name="destijl",
        primary="#0a3ea8", secondary="#d42a1e", accent="#f3c000",
        warning="#f3c000", error="#d42a1e", success="#0a3ea8",
        background="#fcfbf7", surface="#fcfbf7", panel="#efece3", foreground="#111214",
        dark=False,
        variables={
            "block-cursor-background": "#f3c000", "block-cursor-foreground": "#111214",
            "block-cursor-text-style": "bold", "footer-key-foreground": "#d42a1e",
            "border": "#111214", "border-blurred": "#111214",
            "input-cursor-background": "#111214", "input-cursor-foreground": "#fcfbf7",
        },
    ),
    Theme(
        name="graph",
        primary="#14213d", secondary="#d42a1e", accent="#0a3ea8",
        warning="#f3c000", error="#d42a1e", success="#0a3ea8",
        background="#faf8f2", surface="#faf8f2", panel="#e8eaee", foreground="#14213d",
        dark=False,
        variables={
            "block-cursor-background": "#b9c7d6", "block-cursor-foreground": "#14213d",
            "block-cursor-text-style": "bold", "footer-key-foreground": "#d42a1e",
            "border": "#14213d", "border-blurred": "#8ba0b6",
        },
    ),
    Theme(
        name="destijl-dark",
        primary="#5a86e8", secondary="#f0574a", accent="#f2cc4a",
        warning="#f2cc4a", error="#f0574a", success="#5a86e8",
        background="#0e0f11", surface="#0e0f11", panel="#17181b", foreground="#f1efe8",
        dark=True,
        variables={
            "block-cursor-background": "#f2cc4a", "block-cursor-foreground": "#0e0f11",
            "block-cursor-text-style": "bold", "footer-key-foreground": "#f0574a",
            "border": "#f1efe8", "border-blurred": "#5b5d61",
        },
    ),
]
THEME_NAMES = [t.name for t in THEMES]


def _fmt_added(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso).astimezone().strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return iso


class HistoryScreen(Screen[None]):
    """Papers already read (or deleted), newest first. Restore pushes one back on the stack."""

    BINDINGS = [
        Binding("r,enter", "restore", "Re-add to stack"),
        Binding("o", "open", "Open PDF"),
        Binding("x", "forget", "Forget"),
        Binding("escape,h,q", "app.pop_screen", "Back"),
    ]

    def __init__(self, stack: Stack) -> None:
        super().__init__()
        self.stack = stack

    def compose(self) -> ComposeResult:
        yield Header()
        yield DataTable(id="history", cursor_type="row", zebra_stripes=True)
        yield Static("No papers read yet.", id="empty")
        yield Footer()

    def on_mount(self) -> None:
        self.sub_title = "history"
        table = self.query_one(DataTable)
        table.add_column("Title", key="title")
        table.add_column("Read", key="read", width=16)
        self.refresh_table()
        table.focus()

    def refresh_table(self) -> None:
        table = self.query_one(DataTable)
        cursor = table.cursor_row
        table.clear()
        papers = self.stack.history_newest_first()
        for paper in papers:
            table.add_row(paper.title, _fmt_added(paper.read_at or ""), key=paper.url)
        table.display = bool(papers)
        self.query_one("#empty", Static).display = not papers
        if papers:
            table.move_cursor(row=min(cursor, len(papers) - 1))

    def _highlighted(self) -> Paper | None:
        table = self.query_one(DataTable)
        if not table.row_count:
            return None
        key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key
        return self.stack.find_history(str(key.value))

    def action_restore(self) -> None:
        paper = self._highlighted()
        if paper is None:
            return
        try:
            self.stack.restore(paper.url)
        except RemoteError as exc:
            self.notify(str(exc), severity="error")
            return
        self.stack.save()
        self.refresh_table()
        self.app.refresh_table()
        self.notify(f"Back on the stack: {paper.title}")

    def action_open(self) -> None:
        paper = self._highlighted()
        if paper is not None:
            opener.open_url(paper.read_url)

    def action_forget(self) -> None:
        paper = self._highlighted()
        if paper is None:
            return
        try:
            self.stack.forget(paper.url)
        except RemoteError as exc:
            self.notify(str(exc), severity="error")
            return
        self.stack.save()
        self.refresh_table()


class PaperQueue(App[None]):
    TITLE = "paperqueue"
    CSS_PATH = "app.tcss"
    BINDINGS = [
        Binding("p", "pop_top", "Pop top"),
        Binding("b", "take_bottom", "Take bottom"),
        Binding("o", "peek", "Open (keep)"),
        Binding("O", "peek_page", "Open page", key_display="O"),
        Binding("d", "delete", "Delete"),
        Binding("a", "focus_input", "Add"),
        Binding("h", "history", "History"),
        Binding("t", "cycle_theme", "Theme"),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(self, stack: Stack | RemoteStack) -> None:
        super().__init__()
        self.stack = stack
        self._problem: str | None = None

    # -- layout ------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Header()
        yield Input(placeholder="Paste a paper link and press Enter", id="link")
        yield DataTable(id="stack", cursor_type="row", zebra_stripes=True)
        yield Static("Stack is empty. Paste a link above.", id="empty")
        yield Footer()

    def on_mount(self) -> None:
        for theme in THEMES:
            self.register_theme(theme)
        saved = load_theme()
        self.theme = saved if saved in THEME_NAMES else THEME_NAMES[0]
        table = self.query_one(DataTable)
        table.add_column("#", key="idx", width=4)
        table.add_column("Title", key="title")
        table.add_column("PDF", key="pdf", width=5)
        table.add_column("Added", key="added", width=16)
        self.refresh_table()
        self.query_one(Input).focus()
        if self.stack.remote:
            self.set_interval(POLL_SECONDS, self.poll)
        else:
            for paper in self.stack:
                if paper.needs_resolve:
                    self.resolve_paper(paper.url, paper.arxiv_id)

    @work(thread=True, group="poll", exclusive=True)
    def poll(self) -> None:
        try:
            changed = self.stack.refresh()
        except RemoteError as exc:
            self.call_from_thread(self._set_problem, str(exc))
            return
        self.call_from_thread(self._set_problem, None)
        if changed:
            self.call_from_thread(self.refresh_table)

    def _set_problem(self, problem: str | None) -> None:
        if problem != self._problem:
            self._problem = problem
            self.refresh_table()

    def refresh_table(self) -> None:
        main = self.screen_stack[0]
        table = main.query_one("#stack", DataTable)
        empty = main.query_one("#empty", Static)
        cursor = table.cursor_row
        table.clear()
        papers = self.stack.top_first()
        for i, paper in enumerate(papers, start=1):
            pdf = "yes" if paper.pdf_url else "-"
            table.add_row(str(i), paper.title, pdf, _fmt_added(paper.added_at), key=paper.url)
        table.display = bool(papers)
        empty.display = not papers
        if papers:
            table.move_cursor(row=min(cursor, len(papers) - 1))
        mode = "synced" if self.stack.remote else "local"
        self.sub_title = f"{len(papers)} paper{'s' if len(papers) != 1 else ''} · {self._problem or mode}"

    # -- adding ------------------------------------------------------------

    def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        if not text:
            self.query_one(DataTable).focus()
            return
        try:
            url, arxiv_id = links.normalize(text)
        except ValueError as exc:
            self.notify(str(exc), severity="error")
            return
        paper = Paper(url=url, arxiv_id=arxiv_id)
        try:
            pushed = self.stack.push(paper)
        except RemoteError as exc:
            self.notify(str(exc), severity="error")
            return
        if not pushed:
            self.notify("Already on the stack", severity="warning")
            event.input.clear()
            return
        self.stack.save()
        event.input.clear()
        self.refresh_table()
        if not self.stack.remote:
            self.resolve_paper(url, arxiv_id)

    @work(thread=True, group="resolve")
    def resolve_paper(self, url: str, arxiv_id: str | None) -> None:
        res = links.resolve(url, arxiv_id)
        if res.title or res.pdf_url or res.arxiv_id:
            self.call_from_thread(self._apply_resolved, url, res)

    def _apply_resolved(self, url: str, res: links.Resolved) -> None:
        if self.stack.update(url, title=res.title, pdf_url=res.pdf_url, arxiv_id=res.arxiv_id):
            self.stack.save()
            self.refresh_table()

    # -- reading -----------------------------------------------------------

    def _read(self, paper: Paper | None, where: str) -> None:
        if paper is None:
            self.notify("Stack is empty", severity="warning")
            return
        self.stack.save()
        self.refresh_table()
        opener.open_url(paper.read_url)
        self.notify(f"Reading from {where}: {paper.title}")

    def action_pop_top(self) -> None:
        try:
            self._read(self.stack.pop(), "top")
        except RemoteError as exc:
            self.notify(str(exc), severity="error")

    def action_take_bottom(self) -> None:
        try:
            self._read(self.stack.take_bottom(), "bottom")
        except RemoteError as exc:
            self.notify(str(exc), severity="error")

    def _highlighted(self) -> Paper | None:
        table = self.query_one(DataTable)
        if not table.row_count:
            return None
        key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key
        return self.stack.find(str(key.value))

    def action_peek(self) -> None:
        self._open_highlighted(pdf=True)

    def action_peek_page(self) -> None:
        self._open_highlighted(pdf=False)

    def _open_highlighted(self, pdf: bool) -> None:
        paper = self._highlighted()
        if paper is None:
            self.notify("Nothing selected", severity="warning")
            return
        opener.open_url(paper.read_url if pdf else paper.url)
        self.notify(f"Opened: {paper.title}")

    def action_delete(self) -> None:
        paper = self._highlighted()
        if paper is None:
            self.notify("Nothing selected", severity="warning")
            return
        try:
            self.stack.remove(paper.url)
        except RemoteError as exc:
            self.notify(str(exc), severity="error")
            return
        self.stack.save()
        self.refresh_table()
        self.notify(f"Removed: {paper.title}")

    def action_cycle_theme(self) -> None:
        """Cycle the De Stijl palettes, mirroring `t` in the web client."""
        i = THEME_NAMES.index(self.theme) if self.theme in THEME_NAMES else -1
        self.theme = THEME_NAMES[(i + 1) % len(THEME_NAMES)]
        save_theme(self.theme)
        self.notify(f"theme: {self.theme}")

    def action_focus_input(self) -> None:
        self.query_one(Input).focus()

    def action_history(self) -> None:
        self.push_screen(HistoryScreen(self.stack))

    def on_key(self, event) -> None:
        if event.key == "escape" and self.query_one(Input).has_focus:
            table = self.query_one(DataTable)
            if table.display:
                table.focus()


def open_stack() -> Stack | RemoteStack:
    config = load_server_config()
    return RemoteStack(config) if config else Stack.load()


def cmd_import() -> int:
    config = load_server_config()
    if config is None:
        print("paperqueue import: no server configured (set PAPERQUEUE_URL/PAPERQUEUE_TOKEN "
              "or ~/.config/paperqueue/config.toml)", file=sys.stderr)
        return 2
    local = Stack.load()
    remote = RemoteStack(config)
    try:
        summary = remote.import_local(local)
    except RemoteError as exc:
        print(f"paperqueue import: {exc}", file=sys.stderr)
        return 1
    print(f"Imported {summary.get('added_papers', 0)} papers and {summary.get('added_history', 0)} "
          f"history entries from {local.path} to {config.url}; the server now has {len(remote)} papers.")
    return 0


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="paperqueue", description="A stack of papers to read.")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("import", help="push the local stack.json to the configured server (one-time migration)")
    args = parser.parse_args(argv)
    if args.command == "import":
        sys.exit(cmd_import())
    try:
        stack = open_stack()
        if stack.remote:
            stack.refresh()
    except (RemoteError, ValueError) as exc:
        print(f"paperqueue: {exc}", file=sys.stderr)
        sys.exit(1)
    PaperQueue(stack).run()


if __name__ == "__main__":
    main()
