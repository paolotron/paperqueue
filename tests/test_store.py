from pathlib import Path

import pytest

from paperqueue.store import Paper, Stack


def test_pop_is_lifo_and_take_bottom_is_fifo():
    s = Stack()
    for u in ("a", "b", "c"):
        assert s.push(Paper(url=u))
    assert [p.url for p in s.top_first()] == ["c", "b", "a"]
    assert s.pop("Useful overview", 4.2).url == "c"
    assert s.take_bottom("Too introductory", 1.5).url == "a"
    assert [p.url for p in s] == ["b"]
    assert s.pop("Good references", 3).url == "b"
    assert s.pop("Nothing to read", 0) is None
    assert s.take_bottom("Nothing to read", 0) is None


def test_duplicate_push_is_noop():
    s = Stack()
    assert s.push(Paper(url="x"))
    assert not s.push(Paper(url="x"))
    assert len(s) == 1


def test_reading_requires_a_comment_and_a_continuous_0_to_5_score():
    s = Stack()
    s.push(Paper(url="x"))
    with pytest.raises(ValueError, match="comment"):
        s.pop("   ", 2.5)
    with pytest.raises(ValueError, match="between 0 and 5"):
        s.pop("Fine", 5.1)
    assert s.find("x") is not None

    paper = s.pop("A precise and useful contribution", 3.7)
    assert paper is not None
    assert paper.review_comment == "A precise and useful contribution"
    assert paper.usefulness == 3.7


def test_title_defaults_to_url_and_update():
    s = Stack()
    s.push(Paper(url="u"))
    assert s.find("u").title == "u"
    assert s.update_title("u", "T")
    assert s.find("u").title == "T"
    assert not s.update_title("missing", "T")


def test_save_load_roundtrip_atomic(tmp_path: Path):
    path = tmp_path / "nested" / "stack.json"
    s = Stack(path=path)
    s.push(Paper(url="https://arxiv.org/abs/1706.03762", title="Attention", arxiv_id="1706.03762"))
    s.push(Paper(url="https://example.com/p"))
    s.save()
    assert path.exists()
    assert [p.name for p in path.parent.iterdir()] == ["stack.json"]

    loaded = Stack.load(path)
    assert [p.url for p in loaded] == [p.url for p in s]
    assert loaded.find("https://arxiv.org/abs/1706.03762").title == "Attention"
    assert loaded.find("https://arxiv.org/abs/1706.03762").arxiv_id == "1706.03762"
    assert loaded.remove("https://example.com/p") is not None
    loaded.save()
    assert len(Stack.load(path)) == 1


def test_load_missing_file_is_empty(tmp_path: Path):
    s = Stack.load(tmp_path / "nope.json")
    assert len(s) == 0


def test_read_url_and_needs_resolve():
    p = Paper(url="https://x.org/p")
    assert p.read_url == "https://x.org/p"
    assert p.needs_resolve
    s = Stack()
    s.push(p)
    assert s.update("https://x.org/p", title="T", pdf_url="https://x.org/p.pdf", arxiv_id="1")
    assert p.read_url == "https://x.org/p.pdf"
    assert p.arxiv_id == "1"
    assert not p.needs_resolve
    assert not s.update("missing", title="T")


def test_read_and_deleted_papers_go_to_history_and_can_be_restored(tmp_path: Path):
    path = tmp_path / "stack.json"
    s = Stack(path=path)
    for u in ("a", "b", "c"):
        s.push(Paper(url=u))
    s.pop("Review c", 4.5)            # c
    s.take_bottom("Review a", 2.5)    # a
    s.remove("b")
    assert len(s) == 0
    assert [p.url for p in s.history_newest_first()] == ["b", "a", "c"]
    assert all(p.read_at for p in s.history_newest_first())

    s.save()
    s = Stack.load(path)
    assert [p.url for p in s.history_newest_first()] == ["b", "a", "c"]

    restored = s.restore("a")
    assert restored is not None and restored.read_at is None
    assert restored.review_comment is None and restored.usefulness is None
    assert [p.url for p in s.top_first()] == ["a"]
    assert [p.url for p in s.history_newest_first()] == ["b", "c"]
    assert s.restore("missing") is None

    # Reading it again replaces the old history entry rather than duplicating it.
    s.pop("Updated review", 3.4)
    assert [p.url for p in s.history_newest_first()] == ["a", "b", "c"]
    assert s.forget("c").url == "c"
    assert [p.url for p in s.history_newest_first()] == ["a", "b"]


def test_restore_when_url_already_queued_just_drops_history():
    s = Stack()
    s.push(Paper(url="a"))
    s.pop("Initial review", 2.0)
    s.push(Paper(url="a"))
    assert s.restore("a") is not None
    assert len(s) == 1 and s.history_newest_first() == []


def test_history_is_capped():
    s = Stack()
    s.MAX_HISTORY = 3
    for i in range(5):
        s.push(Paper(url=str(i)))
        s.pop("A comment", 2.0)
    assert [p.url for p in s.history_newest_first()] == ["4", "3", "2"]


def test_conference_is_filled_by_resolution_but_a_manual_value_wins(tmp_path: Path):
    s = Stack(path=tmp_path / "s.json")
    s.push(Paper(url="a"))
    assert s.update("a", conference="CVPR 2025")
    assert s.find("a").conference == "CVPR 2025"
    s.set_conference("a", "  ECCV 2026 ")
    s.update("a", conference="CVPR 2025")
    assert s.find("a").conference == "ECCV 2026"

    s.pop("read it", 3)
    assert s.set_conference("a", "ICML").conference == "ICML"  # history is editable too
    assert s.set_conference("a", "   ").conference is None
    assert s.set_conference("missing", "ICML") is None

    s.set_conference("a", "NeurIPS 2023")
    s.save()
    loaded = Stack.load(tmp_path / "s.json")
    assert loaded.find_history("a").conference == "NeurIPS 2023"
    assert loaded.restore("a").conference == "NeurIPS 2023"  # it describes the paper, not the reading


def test_old_files_without_conference_load():
    assert Paper.from_dict({"url": "a"}).conference is None


def test_conference_backfill_runs_once_per_paper_and_never_touches_other_fields(tmp_path: Path):
    s = Stack(path=tmp_path / "s.json")
    for url in ("done", "unresolved", "typed", "read"):
        s.push(Paper(url=url, title=f"Title {url}", pdf_url=f"{url}.pdf"))
    s.find("unresolved").pdf_url = None  # the full resolution will look for its venue
    s.set_conference("typed", "ICML 2024")
    s.find("done").conference_checked = True
    s.take_bottom("read it", 3)  # "done" goes to history
    s.pop("read it", 3)  # "read" goes to history
    assert [p.url for p in s.backfill_candidates()] == ["read"]  # "done" checked, "unresolved" left to resolution
    s.find_history("done").conference_checked = False
    assert [p.url for p in s.backfill_candidates()] == ["done", "read"]

    assert s.backfill_conference("read", "CVPR 2025")
    assert s.backfill_conference("done", None)  # the lookup answered but found no venue
    assert not s.backfill_conference("missing", "CVPR 2025")
    assert s.backfill_candidates() == []
    assert s.find_history("read").conference == "CVPR 2025"
    assert s.find_history("read").title == "Title read"
    assert s.find_history("done").conference is None

    s.backfill_conference("typed", "NeurIPS 2023")
    assert s.find("typed").conference == "ICML 2024"

    s.save()
    assert Stack.load(tmp_path / "s.json").backfill_candidates() == []


def test_resolution_marks_the_conference_checked_only_when_it_answered():
    s = Stack()
    s.push(Paper(url="a"))
    s.update("a", pdf_url="a.pdf")  # e.g. arXiv throttled: only the deterministic PDF came back
    assert s.find("a").needs_conference
    s.update("a", title="A real title")
    assert not s.find("a").needs_conference
    assert Paper.from_dict({"url": "b"}).needs_conference


def test_rename_sticks_through_resolution(tmp_path: Path):
    s = Stack(path=tmp_path / "s.json")
    s.push(Paper(url="a"))
    assert s.set_title("a", "  My name  ").title == "My name"
    s.update("a", title="Resolved title", pdf_url="a.pdf")
    assert s.find("a").title == "My name"
    assert s.find("a").pdf_url == "a.pdf"
    assert s.set_title("a", "   ").title == "My name"  # empty keeps the title

    s.pop("read it", 3)
    assert s.set_title("a", "Renamed later").title == "Renamed later"  # history too
    assert s.set_title("missing", "x") is None
    s.save()
    assert Stack.load(tmp_path / "s.json").find_history("a").title_edited


def test_old_files_without_title_edited_load():
    assert Paper.from_dict({"url": "a"}).title_edited is False
