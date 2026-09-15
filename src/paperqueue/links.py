"""URL normalization, title lookup, and PDF resolution."""

from __future__ import annotations

import html
import re
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import quote, urljoin, urlparse

USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) paperqueue/0.1"
TIMEOUT = 8.0
MAX_HTML = 512 * 1024

# New-style ids (2007+): YYMM.NNNNN[vN]; old-style: archive/NNNNNNN[vN]
_ARXIV_ID = r"(?:\d{4}\.\d{4,5}|[a-z\-]+(?:\.[A-Z]{2})?/\d{7})(?:v\d+)?"
_BARE_ID_RE = re.compile(rf"^(?:arxiv:)?({_ARXIV_ID})$", re.IGNORECASE)
_PATH_ID_RE = re.compile(rf"^/(?:abs|pdf|html)/({_ARXIV_ID})(?:\.pdf)?/?$", re.IGNORECASE)
_HREF_ID_RE = re.compile(rf"arxiv\.org/(?:abs|pdf|html)/({_ARXIV_ID})", re.IGNORECASE)
_ARXIV_HOSTS = {"arxiv.org", "www.arxiv.org", "export.arxiv.org", "alphaxiv.org", "www.alphaxiv.org"}
_SCHOLAR_INBOX_HOSTS = {"scholar-inbox.com", "www.scholar-inbox.com"}
_WS_RE = re.compile(r"\s+")
_BOT_WALL_RE = re.compile(r"just a moment|verifying your browser|attention required|access denied", re.IGNORECASE)
_ATOM = {"a": "http://www.w3.org/2005/Atom"}


@dataclass
class Resolved:
    title: str | None = None
    pdf_url: str | None = None
    arxiv_id: str | None = None


# -- normalization ---------------------------------------------------------


def _strip_version(arxiv_id: str) -> str:
    return re.sub(r"v\d+$", "", arxiv_id)


def arxiv_abs(arxiv_id: str) -> str:
    return f"https://arxiv.org/abs/{arxiv_id}"


def arxiv_pdf(arxiv_id: str) -> str:
    return f"https://arxiv.org/pdf/{arxiv_id}"


def normalize(raw: str) -> tuple[str, str | None]:
    """Return (canonical_url, arxiv_id). arxiv_id is None for non-arXiv links.

    arXiv links in abs/pdf/html form (with or without version, .pdf suffix) become
    ``https://arxiv.org/abs/<id>``. Bare ids like ``2401.12345`` are accepted.
    Other strings are returned as URLs, with ``https://`` prepended if no scheme.
    """
    text = raw.strip()
    if not text:
        raise ValueError("empty link")

    m = _BARE_ID_RE.match(text)
    if m:
        aid = _strip_version(m.group(1))
        return arxiv_abs(aid), aid

    candidate = text if "://" in text else f"https://{text}"
    parsed = urlparse(candidate)
    if parsed.netloc.lower() in _ARXIV_HOSTS:
        m = _PATH_ID_RE.match(parsed.path)
        if m:
            aid = _strip_version(m.group(1))
            return arxiv_abs(aid), aid

    host = parsed.netloc.split("@")[-1].split(":")[0]
    if not host or " " in text or ("." not in host and host != "localhost"):
        raise ValueError(f"not a link: {raw!r}")
    return candidate, None


# -- network ---------------------------------------------------------------


def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return resp.read(MAX_HTML)


# -- arXiv API -------------------------------------------------------------


def _clean(text: str) -> str:
    return _WS_RE.sub(" ", html.unescape(text)).strip()


def parse_arxiv_entries(atom_xml: str | bytes) -> list[tuple[str, str]]:
    """Return [(arxiv_id, title), ...] from an arXiv API Atom response."""
    root = ET.fromstring(atom_xml)
    out: list[tuple[str, str]] = []
    for entry in root.findall("a:entry", _ATOM):
        title = _clean(entry.findtext("a:title", default="", namespaces=_ATOM))
        m = _HREF_ID_RE.search(entry.findtext("a:id", default="", namespaces=_ATOM))
        # The API returns a single entry titled "Error" for unknown ids.
        if title and title != "Error" and m:
            out.append((_strip_version(m.group(1)), title))
    return out


def parse_arxiv_title(atom_xml: str | bytes) -> str | None:
    entries = parse_arxiv_entries(atom_xml)
    return entries[0][1] if entries else None


def arxiv_lookup(arxiv_id: str) -> str | None:
    return parse_arxiv_title(_get(f"https://export.arxiv.org/api/query?id_list={arxiv_id}"))


def arxiv_title(arxiv_id: str) -> str | None:
    """Title for an arXiv id: the API first, then the abs page when the API is throttled or down. Never raises."""
    try:
        title = arxiv_lookup(arxiv_id)
        if title:
            return title
    except Exception:
        pass  # export.arxiv.org rate-limits (HTTP 429); the abs page carries citation_title too.
    try:
        title = parse_page(_get(arxiv_abs(arxiv_id)), arxiv_abs(arxiv_id)).title
        return title if title and not _looks_generic(title) else None
    except Exception:
        return None


def _title_words(text: str) -> list[str]:
    return [w.lower() for w in re.findall(r"[A-Za-z0-9]+", text)]


_STOP_WORDS = {"a", "an", "and", "as", "at", "by", "for", "from", "in", "is", "of", "on", "or", "the", "to", "via", "with"}


def arxiv_search_title(words: list[str]) -> tuple[str, str] | None:
    """Find an arXiv paper whose title contains every word, in order. Returns (id, title).

    Tries an exact phrase first, then an AND of the content words (arXiv's search
    chokes on stop words such as "for").
    """
    words = [w for w in words if w]
    if not words:
        return None
    phrase = quote(" ".join(words[:10]))
    content = [w for w in words if w not in _STOP_WORDS and len(w) > 1][:8]
    queries = [f'ti:%22{phrase}%22']
    if content:
        queries.append("+AND+".join(f"ti:{quote(w)}" for w in content))
    for query in queries:
        data = _get(f"https://export.arxiv.org/api/query?search_query={query}&max_results=5")
        for aid, title in parse_arxiv_entries(data):
            if " ".join(words) in " ".join(_title_words(title)):
                return aid, title
    return None


# -- HTML page parsing -----------------------------------------------------


class _Page(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.title = ""
        self.h1s: list[str] = []
        self.meta: dict[str, str] = {}
        self.links: list[tuple[str, str]] = []  # (href, text)
        self._stack: list[str] = []
        self._buf: list[str] = []
        self._href: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k: (v or "") for k, v in attrs}
        if tag == "meta":
            key = (a.get("name") or a.get("property") or "").lower()
            if key and "content" in a and key not in self.meta:
                self.meta[key] = _clean(a["content"])
        elif tag in ("title", "h1"):
            self._stack.append(tag)
            self._buf = []
        elif tag == "a" and a.get("href"):
            self._href = a["href"]
            self._buf = []

    def handle_data(self, data: str) -> None:
        if self._stack or self._href is not None:
            self._buf.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in ("title", "h1") and self._stack and self._stack[-1] == tag:
            self._stack.pop()
            text = _clean("".join(self._buf))
            if tag == "title" and not self.title:
                self.title = text
            elif tag == "h1" and text:
                self.h1s.append(text)
        elif tag == "a" and self._href is not None:
            self.links.append((self._href, _clean("".join(self._buf))))
            self._href = None


def _is_pdf_href(href: str) -> bool:
    path = urlparse(href).path.lower()
    return path.endswith(".pdf") or "/pdf/" in path


def parse_page(page: bytes, base_url: str) -> Resolved:
    """Extract title, PDF link and arXiv id from an HTML page (no network)."""
    p = _Page()
    p.feed(page.decode("utf-8", errors="replace"))
    res = Resolved()

    # Title: citation metadata, then a heading that the <title> repeats
    # (conference sites prefix the title, e.g. "ECCV Poster <title>"),
    # then og:title, then <title>.
    candidates = [p.meta.get("citation_title"), p.meta.get("dc.title")]
    if p.title:
        candidates += [h for h in sorted(p.h1s, key=len, reverse=True) if h in p.title]
    candidates += [p.meta.get("og:title"), p.title]
    for cand in candidates:
        if cand and not _BOT_WALL_RE.search(cand):
            res.title = cand
            break

    # arXiv id from any arxiv.org/abs|pdf link or citation metadata.
    for href, _ in p.links:
        m = _HREF_ID_RE.search(href)
        if m:
            res.arxiv_id = _strip_version(m.group(1))
            break
    if not res.arxiv_id:
        m = _BARE_ID_RE.match(p.meta.get("citation_arxiv_id", ""))
        if m:
            res.arxiv_id = _strip_version(m.group(1))

    # PDF: citation_pdf_url, then a link whose text mentions PDF, then any .pdf link.
    pdf = p.meta.get("citation_pdf_url")
    if not pdf:
        pdf_links = [(h, t) for h, t in p.links if _is_pdf_href(h)]
        preferred = [h for h, t in pdf_links if "pdf" in t.lower()]
        pdf = preferred[0] if preferred else (pdf_links[0][0] if pdf_links else None)
    if pdf:
        res.pdf_url = urljoin(base_url, pdf)
    elif res.arxiv_id:
        res.pdf_url = arxiv_pdf(res.arxiv_id)
    return res


def parse_html_title(page: bytes) -> str | None:
    return parse_page(page, "").title


# -- site-specific ---------------------------------------------------------


def scholar_inbox_slug_words(url: str) -> list[str] | None:
    """Slugs look like '/paper/<Author><Year>[<VENUE>]_<Title_words>'; return the title words."""
    parsed = urlparse(url)
    if parsed.netloc.lower() not in _SCHOLAR_INBOX_HOSTS:
        return None
    m = re.match(r"^/paper/[^/_]*\d{4}[^/_]*_(.+?)/?$", parsed.path)
    if not m:
        return None
    words = _title_words(m.group(1))
    return words or None


def openreview_pdf(url: str) -> str | None:
    """openreview.net/forum?id=X -> openreview.net/pdf?id=X (site blocks scripted fetches)."""
    parsed = urlparse(url)
    if parsed.netloc.lower() not in {"openreview.net", "www.openreview.net"}:
        return None
    m = re.search(r"(?:^|&)id=([A-Za-z0-9_-]+)", parsed.query)
    if not m or parsed.path not in ("/forum", "/pdf", "/attachment"):
        return None
    return f"https://openreview.net/pdf?id={m.group(1)}"


# -- entry point -----------------------------------------------------------


def resolve(url: str, arxiv_id: str | None = None) -> Resolved:
    """Blocking. Best-effort title / PDF / arXiv id for a link. Never raises."""
    res = Resolved(arxiv_id=arxiv_id)
    try:
        if arxiv_id:
            res.pdf_url = arxiv_pdf(arxiv_id)  # deterministic: set before any network call can fail
            res.title = arxiv_title(arxiv_id)
            return res

        words = scholar_inbox_slug_words(url)
        if words:
            hit = arxiv_search_title(words)
            if hit:
                res.arxiv_id, res.title = hit
                res.pdf_url = arxiv_pdf(res.arxiv_id)
                return res

        pdf = openreview_pdf(url)
        if pdf:
            res.pdf_url = pdf

        page = parse_page(_get(url), url)
        res = Resolved(page.title, page.pdf_url or res.pdf_url, page.arxiv_id)
        if res.arxiv_id and (not res.title or _looks_generic(res.title)):
            res.title = arxiv_title(res.arxiv_id) or res.title
        return res
    except Exception:
        return res


def _looks_generic(title: str) -> bool:
    return len(_title_words(title)) < 3


def fetch_title(url: str, arxiv_id: str | None = None) -> str | None:
    return resolve(url, arxiv_id).title
