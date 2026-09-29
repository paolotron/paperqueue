"""RemoteStack and config against an in-process stub of the Worker API."""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from paperqueue.config import ServerConfig, load_server_config
from paperqueue.remote import RemoteError, RemoteStack
from paperqueue.store import Paper, Stack

TOKEN = "t0k3n"


class FakeServer:
    """Minimal in-memory implementation of the API contract used by RemoteStack."""

    def __init__(self):
        self.state = {"version": 0, "papers": [], "history": []}
        self.requests = []

    def paper(self, url):
        return {"url": url, "title": url, "added_at": "2026-01-01T00:00:00+00:00", "arxiv_id": None, "pdf_url": None, "read_at": None, "review_comment": None, "usefulness": None}

    def bump(self):
        self.state["version"] += 1

    def handle(self, method, path, query, body):
        self.requests.append((method, path))
        papers, history = self.state["papers"], self.state["history"]
        if method == "GET" and path == "/api/state":
            return 200, self.state
        if method == "POST" and path == "/api/papers":
            url = body["url"]
            if any(p["url"] == url for p in papers):
                return 409, {"paper": None, "state": self.state}
            papers.append(self.paper(url)); self.bump()
            return 201, {"paper": papers[-1], "state": self.state}
        if method == "POST" and path in ("/api/pop", "/api/take"):
            if not papers:
                return 404, {"paper": None, "state": self.state}
            if not body["comment"].strip() or not 0 <= body["usefulness"] <= 5:
                return 400, {"error": "invalid review"}
            if body.get("url") and body["url"] != (papers[-1] if path == "/api/pop" else papers[0])["url"]:
                return 404, {"paper": None, "state": self.state}
            p = papers.pop() if path == "/api/pop" else papers.pop(0)
            p["read_at"] = "2026-01-02T00:00:00+00:00"; p["review_comment"] = body["comment"].strip(); p["usefulness"] = body["usefulness"]; history.append(p); self.bump()
            return 200, {"paper": p, "state": self.state}
        if method == "DELETE" and path == "/api/papers":
            url = query["url"][0]
            p = next((p for p in papers if p["url"] == url), None)
            if not p:
                return 404, {"paper": None, "state": self.state}
            papers.remove(p); history.append(p); self.bump()
            return 200, {"paper": p, "state": self.state}
        if method == "POST" and path == "/api/history/restore":
            p = next((p for p in history if p["url"] == body["url"]), None)
            if not p:
                return 404, {"paper": None, "state": self.state}
            history.remove(p); p["read_at"] = None; papers.append(p); self.bump()
            return 200, {"paper": p, "state": self.state}
        if method == "DELETE" and path == "/api/history":
            p = next((p for p in history if p["url"] == query["url"][0]), None)
            if not p:
                return 404, {"paper": None, "state": self.state}
            history.remove(p); self.bump()
            return 200, {"paper": p, "state": self.state}
        if method == "POST" and path == "/api/import":
            added = 0
            for p in body["papers"]:
                if not any(q["url"] == p["url"] for q in papers + history):
                    papers.append(p); added += 1
            self.bump()
            return 200, {"added_papers": added, "added_history": 0, "state": self.state}
        if method == "PATCH" and path == "/api/papers":
            p = next((p for p in papers + history if p["url"] == body["url"]), None)
            if not p:
                return 404, {"paper": None, "state": self.state}
            p["conference"] = body["conference"].strip() or None; self.bump()
            return 200, {"paper": p, "state": self.state}
        if path == "/api/boom":
            return 500, {"error": "kaboom"}
        return 404, {"error": "not found"}


@pytest.fixture
def server():
    fake = FakeServer()

    class Handler(BaseHTTPRequestHandler):
        def _serve(self):
            if self.headers.get("Authorization") != f"Bearer {TOKEN}":
                status, payload = 401, {"error": "unauthorized"}
            else:
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"null") if length else None
                parsed = urlparse(self.path)
                status, payload = fake.handle(self.command, parsed.path, parse_qs(parsed.query), body)
            data = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        do_GET = do_POST = do_DELETE = do_PATCH = _serve

        def log_message(self, *_):
            pass

    httpd = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    fake.url = f"http://127.0.0.1:{httpd.server_port}"
    yield fake
    httpd.shutdown()


def test_remote_stack_round_trip(server):
    rs = RemoteStack(ServerConfig(url=server.url, token=TOKEN))
    assert rs.refresh() is True
    assert rs.refresh() is False
    assert rs.push(Paper(url="a"))
    assert rs.push(Paper(url="b"))
    assert not rs.push(Paper(url="a"))
    assert [p.url for p in rs.top_first()] == ["b", "a"]
    assert len(rs) == 2 and rs.find("a") is not None

    assert rs.pop("Worth revisiting", 4.4).url == "b"
    assert rs.take_bottom("Useful background", 2.3).url == "a"
    assert rs.pop("Empty stack", 0) is None
    assert [p.url for p in rs.history_newest_first()] == ["a", "b"]
    assert rs.find_history("a") is not None
    assert rs.find_history("a").usefulness == 2.3

    assert rs.set_conference("a", " CVPR 2025 ").conference == "CVPR 2025"
    assert rs.find_history("a").conference == "CVPR 2025"
    assert rs.set_conference("zzz", "ICML") is None
    assert rs.restore("a").url == "a"
    assert [p.url for p in rs.top_first()] == ["a"]
    assert rs.remove("a").url == "a"
    assert rs.forget("a").url == "a"
    assert rs.forget("a") is None
    assert rs.update("a", title="x") is False
    rs.save()  # no-op


def test_remote_stack_import_and_errors(server, tmp_path: Path):
    local = Stack(path=tmp_path / "s.json")
    local.push(Paper(url="x"))
    local.push(Paper(url="y"))
    rs = RemoteStack(ServerConfig(url=server.url, token=TOKEN))
    summary = rs.import_local(local)
    assert summary["added_papers"] == 2
    assert [p.url for p in rs] == ["x", "y"]

    with pytest.raises(RemoteError, match="401"):
        RemoteStack(ServerConfig(url=server.url, token="wrong")).refresh()
    with pytest.raises(RemoteError, match="kaboom"):
        rs._call("GET", "/api/boom")
    with pytest.raises(RemoteError, match="cannot reach"):
        RemoteStack(ServerConfig(url="http://127.0.0.1:9", token=TOKEN)).refresh()


def test_load_server_config(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("PAPERQUEUE_URL", raising=False)
    monkeypatch.delenv("PAPERQUEUE_TOKEN", raising=False)
    missing = tmp_path / "config.toml"
    assert load_server_config(missing) is None

    missing.write_text('url = "https://pq.example.workers.dev/"\ntoken = "abc"\n')
    cfg = load_server_config(missing)
    assert cfg == ServerConfig(url="https://pq.example.workers.dev", token="abc")

    missing.write_text('url = "https://pq.example.workers.dev"\n')
    with pytest.raises(ValueError):
        load_server_config(missing)

    monkeypatch.setenv("PAPERQUEUE_URL", "http://localhost:8787/")
    monkeypatch.setenv("PAPERQUEUE_TOKEN", "dev")
    assert load_server_config(missing) == ServerConfig(url="http://localhost:8787", token="dev")
