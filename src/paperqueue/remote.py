"""Stack backed by the paperqueue Worker API. Same interface as store.Stack."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Iterator
from urllib.parse import quote

from paperqueue.config import ServerConfig
from paperqueue.store import Paper, Stack

TIMEOUT = 15.0
# Cloudflare's managed bot rules reject the default "Python-urllib/x.y" agent with a 403.
USER_AGENT = "paperqueue/0.1"


class RemoteError(Exception):
    """The server could not be reached or returned an error."""


class RemoteStack:
    """Thin client: every mutation goes to the server and refreshes the cached state."""

    remote = True

    def __init__(self, config: ServerConfig):
        self.config = config
        self.version = -1
        self._papers: list[Paper] = []
        self._history: list[Paper] = []

    # -- transport ---------------------------------------------------------

    def _call(self, method: str, path: str, body: dict | None = None) -> tuple[int, Any]:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            self.config.url + path,
            data=data,
            method=method,
            headers={
                "Authorization": f"Bearer {self.config.token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": USER_AGENT,
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                return resp.status, json.loads(resp.read() or b"null")
        except urllib.error.HTTPError as e:
            try:
                payload = json.loads(e.read() or b"null")
            except ValueError:
                payload = None
            if e.code in (404, 409):
                return e.code, payload
            if e.code == 401:
                raise RemoteError("server rejected the token (401)") from e
            raise RemoteError(f"server error {e.code}: {payload or e.reason}") from e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise RemoteError(f"cannot reach {self.config.url}: {getattr(e, 'reason', e)}") from e

    def _apply_state(self, state: dict) -> bool:
        changed = state.get("version") != self.version
        self.version = state.get("version", 0)
        self._papers = [Paper.from_dict(p) for p in state.get("papers", [])]
        self._history = [Paper.from_dict(p) for p in state.get("history", [])]
        return changed

    def _mutate(self, method: str, path: str, body: dict | None = None) -> tuple[int, Paper | None]:
        status, payload = self._call(method, path, body)
        if isinstance(payload, dict) and "state" in payload:
            self._apply_state(payload["state"])
        paper = payload.get("paper") if isinstance(payload, dict) else None
        return status, (Paper.from_dict(paper) if paper else None)

    def refresh(self) -> bool:
        """Fetch the current state. Returns True if it changed since the last call."""
        _, state = self._call("GET", "/api/state")
        return self._apply_state(state)

    def save(self) -> None:  # the server persists everything
        pass

    # -- stack ops (same contract as store.Stack) --------------------------

    def push(self, paper: Paper) -> bool:
        status, _ = self._mutate("POST", "/api/papers", {"url": paper.url})
        return status != 409

    def pop(self, comment: str, usefulness: float, expected_url: str | None = None) -> Paper | None:
        body: dict[str, Any] = {"comment": comment, "usefulness": usefulness}
        if expected_url is not None:
            body["url"] = expected_url
        return self._mutate("POST", "/api/pop", body)[1]

    def take_bottom(self, comment: str, usefulness: float, expected_url: str | None = None) -> Paper | None:
        body: dict[str, Any] = {"comment": comment, "usefulness": usefulness}
        if expected_url is not None:
            body["url"] = expected_url
        return self._mutate("POST", "/api/take", body)[1]

    def remove(self, url: str) -> Paper | None:
        return self._mutate("DELETE", f"/api/papers?url={quote(url, safe='')}")[1]

    def restore(self, url: str) -> Paper | None:
        return self._mutate("POST", "/api/history/restore", {"url": url})[1]

    def forget(self, url: str) -> Paper | None:
        return self._mutate("DELETE", f"/api/history?url={quote(url, safe='')}")[1]

    def set_conference(self, url: str, conference: str) -> Paper | None:
        return self._mutate("PATCH", "/api/papers", {"url": url, "conference": conference})[1]

    def set_title(self, url: str, title: str) -> Paper | None:
        return self._mutate("PATCH", "/api/papers", {"url": url, "title": title})[1]

    def update(self, url: str, **_: Any) -> bool:  # resolution happens server-side
        return False

    def import_local(self, local: Stack) -> dict:
        """Merge a local stack file into the server. Returns the server's summary."""
        body = {
            "papers": [p.to_dict() for p in local],
            "history": [p.to_dict() for p in local.history_oldest_first()],
        }
        _, payload = self._call("POST", "/api/import", body)
        if isinstance(payload, dict) and "state" in payload:
            self._apply_state(payload["state"])
        return payload if isinstance(payload, dict) else {}

    # -- reads from the cached state ---------------------------------------

    def find(self, url: str) -> Paper | None:
        return next((p for p in self._papers if p.url == url), None)

    def find_history(self, url: str) -> Paper | None:
        return next((p for p in self._history if p.url == url), None)

    def top_first(self) -> list[Paper]:
        return list(reversed(self._papers))

    def history_newest_first(self) -> list[Paper]:
        return list(reversed(self._history))

    def __iter__(self) -> Iterator[Paper]:
        return iter(self._papers)

    def __len__(self) -> int:
        return len(self._papers)
