from pathlib import Path

from paperqueue.store import Paper, Stack


def test_pop_is_lifo_and_take_bottom_is_fifo():
    s = Stack()
    for u in ("a", "b", "c"):
        assert s.push(Paper(url=u))
    assert [p.url for p in s.top_first()] == ["c", "b", "a"]
    assert s.pop().url == "c"
    assert s.take_bottom().url == "a"
    assert [p.url for p in s] == ["b"]
    assert s.pop().url == "b"
    assert s.pop() is None
    assert s.take_bottom() is None


def test_duplicate_push_is_noop():
    s = Stack()
    assert s.push(Paper(url="x"))
    assert not s.push(Paper(url="x"))
    assert len(s) == 1


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
    s.pop()            # c
    s.take_bottom()    # a
    s.remove("b")
    assert len(s) == 0
    assert [p.url for p in s.history_newest_first()] == ["b", "a", "c"]
    assert all(p.read_at for p in s.history_newest_first())

    s.save()
    s = Stack.load(path)
    assert [p.url for p in s.history_newest_first()] == ["b", "a", "c"]

    restored = s.restore("a")
    assert restored is not None and restored.read_at is None
    assert [p.url for p in s.top_first()] == ["a"]
    assert [p.url for p in s.history_newest_first()] == ["b", "c"]
    assert s.restore("missing") is None

    # Reading it again replaces the old history entry rather than duplicating it.
    s.pop()
    assert [p.url for p in s.history_newest_first()] == ["a", "b", "c"]
    assert s.forget("c").url == "c"
    assert [p.url for p in s.history_newest_first()] == ["a", "b"]


def test_restore_when_url_already_queued_just_drops_history():
    s = Stack()
    s.push(Paper(url="a"))
    s.pop()
    s.push(Paper(url="a"))
    assert s.restore("a") is not None
    assert len(s) == 1 and s.history_newest_first() == []


def test_history_is_capped():
    s = Stack()
    s.MAX_HISTORY = 3
    for i in range(5):
        s.push(Paper(url=str(i)))
        s.pop()
    assert [p.url for p in s.history_newest_first()] == ["4", "3", "2"]
