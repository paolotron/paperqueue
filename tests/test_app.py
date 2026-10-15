import asyncio

import pytest

from paperqueue import app as tui
from paperqueue.store import Paper, Stack


@pytest.fixture
def run(tmp_path, monkeypatch):
    """Run a scenario against the TUI on a three-paper local stack, with no browser and no network."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    opened: list[str] = []
    monkeypatch.setattr(tui.opener, "open_url", opened.append)
    monkeypatch.setattr(tui.PaperQueue, "resolve_paper", lambda *a, **k: None)
    monkeypatch.setattr(tui.PaperQueue, "backfill_conferences", lambda *a, **k: None)
    stack = Stack(path=tmp_path / "stack.json")
    for i in range(3):
        stack.push(Paper(url=f"https://example.com/{i}", title=f"P{i}"))

    def go(scenario):
        async def main():
            app = tui.PaperQueue(stack)
            async with app.run_test() as pilot:
                await pilot.press("escape")  # leave the link input
                await scenario(app, pilot)

        asyncio.run(main())
        return stack, opened

    return go


def test_history_screen_does_not_act_on_the_hidden_stack(run):
    async def scenario(app, pilot):
        await pilot.press("h", "d", "p", "b", "a")
        await pilot.pause()
        assert isinstance(app.screen, tui.HistoryScreen)
        assert app._reviewing is None

    stack, opened = run(scenario)
    assert len(stack) == 3
    assert opened == []


def test_escape_cancels_a_review_and_keeps_the_paper(run):
    async def scenario(app, pilot):
        await pilot.press("p")
        await pilot.pause()
        assert app._reviewing is not None
        await pilot.press("ctrl+s")  # an empty comment is refused
        await pilot.pause()
        assert app._reviewing is not None
        await pilot.press("escape")
        await pilot.pause()
        assert app._reviewing is None
        assert not app.query(tui.ReviewPanel)

    stack, opened = run(scenario)
    assert [p.url for p in stack.top_first()] == ["https://example.com/2", "https://example.com/1", "https://example.com/0"]
    assert stack.history_newest_first() == []
    assert opened == ["https://example.com/2"]


def test_ctrl_s_saves_the_review(run):
    async def scenario(app, pilot):
        await pilot.press("b", *"solid", "ctrl+s")
        await pilot.pause()
        assert app._reviewing is None

    stack, _ = run(scenario)
    assert len(stack) == 2
    [read] = stack.history_newest_first()
    assert (read.url, read.review_comment, read.usefulness) == ("https://example.com/0", "solid", 2.5)


def test_failed_save_refreshes_the_list_when_the_paper_is_gone(run):
    async def scenario(app, pilot):
        await pilot.press("p")
        await pilot.pause()

        def pop_after_remote_delete(*_):
            # Like the server: the paper was deleted elsewhere, and the 404 brings that newer state.
            app.stack.remove("https://example.com/2")
            return None

        app.stack.pop = pop_after_remote_delete
        await pilot.press(*"late", "ctrl+s")
        await pilot.pause()
        table = app.query_one(tui.DataTable)
        assert table.row_count == 2
        assert str(table.coordinate_to_cell_key((0, 0)).row_key.value) == "https://example.com/1"

    stack, _ = run(scenario)
    assert [p.url for p in stack.top_first()] == ["https://example.com/1", "https://example.com/0"]


def test_escape_in_the_link_input_returns_to_the_review(run):
    async def scenario(app, pilot):
        await pilot.press("p")
        await pilot.pause()
        app.query_one("#link", tui.Input).focus()
        await pilot.press("escape")
        await pilot.pause()
        assert app._reviewing is not None
        assert app.focused is app.query_one("#review-comment")

    run(scenario)
