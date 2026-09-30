"""Paper stack with JSON persistence."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator


MAX_CONFERENCE = 80
MAX_TITLE = 300


def default_path() -> Path:
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "paperqueue" / "stack.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Paper:
    url: str
    title: str = ""
    added_at: str = field(default_factory=_now)
    arxiv_id: str | None = None
    pdf_url: str | None = None
    read_at: str | None = None
    review_comment: str | None = None
    usefulness: float | None = None
    conference: str | None = None
    # Venue detection has run for this paper (or the reader set it); the backfill skips it.
    conference_checked: bool = False
    # The reader renamed the paper; resolution no longer touches the title.
    title_edited: bool = False

    @property
    def read_url(self) -> str:
        """What to open when reading: the PDF if known, else the page."""
        return self.pdf_url or self.url

    @property
    def needs_resolve(self) -> bool:
        return self.pdf_url is None or self.title == self.url

    @property
    def needs_conference(self) -> bool:
        return not self.conference and not self.conference_checked

    def __post_init__(self) -> None:
        if not self.title:
            self.title = self.url

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Paper":
        return cls(
            url=data["url"],
            title=data.get("title", ""),
            added_at=data.get("added_at") or _now(),
            arxiv_id=data.get("arxiv_id"),
            pdf_url=data.get("pdf_url"),
            read_at=data.get("read_at"),
            review_comment=data.get("review_comment"),
            usefulness=data.get("usefulness"),
            conference=data.get("conference") or None,
            conference_checked=data.get("conference_checked") is True,
            title_edited=data.get("title_edited") is True,
        )


class Stack:
    """A stack of papers. Index -1 is the top (most recently pushed)."""

    MAX_HISTORY = 500
    remote = False

    def __init__(
        self,
        papers: list[Paper] | None = None,
        path: Path | None = None,
        history: list[Paper] | None = None,
    ):
        self._papers: list[Paper] = list(papers or [])
        self._history: list[Paper] = list(history or [])  # oldest first
        self.path = path

    # -- persistence -------------------------------------------------------

    @classmethod
    def load(cls, path: Path | None = None) -> "Stack":
        path = path or default_path()
        papers: list[Paper] = []
        history: list[Paper] = []
        if path.exists():
            with path.open("r", encoding="utf-8") as f:
                raw = json.load(f)
            papers = [Paper.from_dict(p) for p in raw.get("papers", [])]
            history = [Paper.from_dict(p) for p in raw.get("history", [])]
        return cls(papers, path, history)

    def save(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "papers": [asdict(p) for p in self._papers],
            "history": [asdict(p) for p in self._history],
        }
        fd, tmp = tempfile.mkstemp(prefix=".stack-", suffix=".tmp", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    # -- stack ops ---------------------------------------------------------

    def push(self, paper: Paper) -> bool:
        """Push a paper. Returns False (and does nothing) if the URL is already queued."""
        if self.find(paper.url) is not None:
            return False
        self._papers.append(paper)
        return True

    @staticmethod
    def _review(comment: str, usefulness: float) -> tuple[str, float]:
        comment = comment.strip()
        if not comment:
            raise ValueError("a review comment is required")
        usefulness = float(usefulness)
        if not 0 <= usefulness <= 5:
            raise ValueError("usefulness must be between 0 and 5")
        return comment, usefulness

    def _archive(self, paper: Paper | None, review: tuple[str, float] | None = None) -> Paper | None:
        """Move a paper that left the stack into history (newest last)."""
        if paper is None:
            return None
        paper.read_at = _now()
        if review is not None:
            paper.review_comment, paper.usefulness = review
        self._history = [h for h in self._history if h.url != paper.url]
        self._history.append(paper)
        del self._history[: -self.MAX_HISTORY]
        return paper

    def pop(self, comment: str, usefulness: float, expected_url: str | None = None) -> Paper | None:
        """Complete the top paper's required review and move it into history."""
        review = self._review(comment, usefulness)
        if expected_url is not None and (not self._papers or self._papers[-1].url != expected_url):
            return None
        return self._archive(self._papers.pop() if self._papers else None, review)

    def take_bottom(self, comment: str, usefulness: float, expected_url: str | None = None) -> Paper | None:
        """Complete the bottom paper's required review and move it into history."""
        review = self._review(comment, usefulness)
        if expected_url is not None and (not self._papers or self._papers[0].url != expected_url):
            return None
        return self._archive(self._papers.pop(0) if self._papers else None, review)

    def remove(self, url: str) -> Paper | None:
        paper = self.find(url)
        if paper is not None:
            self._papers.remove(paper)
        return self._archive(paper)

    # -- history -----------------------------------------------------------

    def history_newest_first(self) -> list[Paper]:
        return list(reversed(self._history))

    def history_oldest_first(self) -> list[Paper]:
        return list(self._history)

    def find_history(self, url: str) -> Paper | None:
        return next((p for p in self._history if p.url == url), None)

    def restore(self, url: str) -> Paper | None:
        """Move a paper from history back onto the top of the stack."""
        paper = self.find_history(url)
        if paper is None:
            return None
        self._history.remove(paper)
        if self.find(url) is not None:
            return paper  # already queued again; just drop the history entry
        paper.read_at = None
        paper.added_at = _now()
        paper.review_comment = None
        paper.usefulness = None
        self._papers.append(paper)
        return paper

    def forget(self, url: str) -> Paper | None:
        """Drop a paper from history for good."""
        paper = self.find_history(url)
        if paper is not None:
            self._history.remove(paper)
        return paper

    def find(self, url: str) -> Paper | None:
        return next((p for p in self._papers if p.url == url), None)

    def update_title(self, url: str, title: str) -> bool:
        return self.update(url, title=title)

    def update(
        self,
        url: str,
        *,
        title: str | None = None,
        pdf_url: str | None = None,
        arxiv_id: str | None = None,
        conference: str | None = None,
    ) -> bool:
        """Apply resolved metadata. Returns False if the paper is gone. Never overwrites a
        title or conference the reader set."""
        paper = self.find(url)
        if paper is None:
            return False
        if title and not paper.title_edited:
            paper.title = title
        if pdf_url:
            paper.pdf_url = pdf_url
        if arxiv_id:
            paper.arxiv_id = arxiv_id
        if conference and not paper.conference:
            paper.conference = conference
        if title or conference:  # the lookup answered, so the venue has been looked for
            paper.conference_checked = True
        return True

    def backfill_candidates(self) -> list[Paper]:
        """Papers from before the Conference column that still need their one venue lookup.

        Queued papers still missing a title or PDF are left to the full resolution.
        """
        queued = [p for p in self._papers if p.needs_conference and not p.needs_resolve]
        return queued + [p for p in self._history if p.needs_conference]

    def backfill_conference(self, url: str, conference: str | None) -> bool:
        """Record a conference-only lookup: fill an empty conference and mark it checked."""
        paper = self.find(url) or self.find_history(url)
        if paper is None:
            return False
        if conference and not paper.conference:
            paper.conference = conference
        paper.conference_checked = True
        return True

    def set_conference(self, url: str, conference: str) -> Paper | None:
        """Set (or clear, with an empty string) the conference of a queued or read paper."""
        paper = self.find(url) or self.find_history(url)
        if paper is not None:
            paper.conference = conference.strip()[:MAX_CONFERENCE] or None
            paper.conference_checked = True
        return paper

    def set_title(self, url: str, title: str) -> Paper | None:
        """Rename a queued or read paper. An empty title leaves it unchanged."""
        paper = self.find(url) or self.find_history(url)
        title = title.strip()[:MAX_TITLE]
        if paper is not None and title:
            paper.title = title
            paper.title_edited = True
        return paper

    def top_first(self) -> list[Paper]:
        return list(reversed(self._papers))

    def __iter__(self) -> Iterator[Paper]:
        return iter(self._papers)

    def __len__(self) -> int:
        return len(self._papers)
