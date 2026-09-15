import pytest

from paperqueue.links import normalize, parse_arxiv_title, parse_html_title

ABS = "https://arxiv.org/abs/1706.03762"


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("https://arxiv.org/abs/1706.03762", (ABS, "1706.03762")),
        ("https://arxiv.org/abs/1706.03762v7", (ABS, "1706.03762")),
        ("https://arxiv.org/pdf/1706.03762", (ABS, "1706.03762")),
        ("https://arxiv.org/pdf/1706.03762v2.pdf", (ABS, "1706.03762")),
        ("http://www.arxiv.org/html/1706.03762v1/", (ABS, "1706.03762")),
        ("arxiv.org/abs/1706.03762", (ABS, "1706.03762")),
        ("  1706.03762  ", (ABS, "1706.03762")),
        ("arXiv:1706.03762v3", (ABS, "1706.03762")),
        ("https://www.alphaxiv.org/abs/1706.03762", (ABS, "1706.03762")),
        ("https://arxiv.org/abs/hep-th/9901001", ("https://arxiv.org/abs/hep-th/9901001", "hep-th/9901001")),
        ("https://openreview.net/forum?id=abc", ("https://openreview.net/forum?id=abc", None)),
        ("openreview.net/forum?id=abc", ("https://openreview.net/forum?id=abc", None)),
        ("https://arxiv.org/list/cs.AI/recent", ("https://arxiv.org/list/cs.AI/recent", None)),
    ],
)
def test_normalize(raw, expected):
    assert normalize(raw) == expected


@pytest.mark.parametrize("raw", ["", "   ", "not a link"])
def test_normalize_rejects(raw):
    with pytest.raises(ValueError):
        normalize(raw)


ATOM = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title type="html">ArXiv Query</title>
  <entry>
    <id>http://arxiv.org/abs/1706.03762v7</id>
    <title>Attention Is All
  You Need</title>
  </entry>
</feed>"""


def test_parse_arxiv_title():
    assert parse_arxiv_title(ATOM) == "Attention Is All You Need"


def test_parse_arxiv_title_error_entry():
    xml = b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Error</title></entry></feed>'
    assert parse_arxiv_title(xml) is None
    assert parse_arxiv_title(b'<feed xmlns="http://www.w3.org/2005/Atom"></feed>') is None


def test_parse_html_title():
    page = b"<html><head><TITLE>\n  Some &amp; Paper \n</TITLE></head></html>"
    assert parse_html_title(page) == "Some & Paper"
    assert parse_html_title(b"<html></html>") is None
    assert parse_html_title(b"<title>Verifying your browser | OpenReview</title>") is None


from paperqueue.links import openreview_pdf, parse_page, parse_arxiv_entries, scholar_inbox_slug_words


def test_openreview_pdf():
    assert openreview_pdf("https://openreview.net/forum?id=bzs4uPLXvi") == "https://openreview.net/pdf?id=bzs4uPLXvi"
    assert openreview_pdf("https://openreview.net/forum?noteId=1&id=abc-D_9") == "https://openreview.net/pdf?id=abc-D_9"
    assert openreview_pdf("https://openreview.net/group?id=ICLR") is None
    assert openreview_pdf("https://example.com/forum?id=x") is None

ECCV = b"""<html><head><title>ECCV Poster JSON: Jigsaw Self-play Optimization for Normalizing Flows</title></head>
<body><h1><a href="#">CSP Test</a></h1><h1 class="event-title">JSON: Jigsaw Self-play Optimization for Normalizing Flows</h1>
<a href="/virtual/2026/papers.html">All papers</a>
<a class="action-btn" href="https://media.eventhosts.cc/Conferences/ECCV2026/pdfs/2253.pdf" target="_blank">
  <i class="fas fa-file-pdf"></i> Paper PDF</a></body></html>"""


def test_parse_page_eccv_poster():
    res = parse_page(ECCV, "https://eccv.ecva.net/virtual/2026/poster/3623")
    assert res.title == "JSON: Jigsaw Self-play Optimization for Normalizing Flows"
    assert res.pdf_url == "https://media.eventhosts.cc/Conferences/ECCV2026/pdfs/2253.pdf"
    assert res.arxiv_id is None


def test_parse_page_citation_meta_wins():
    page = b"""<html><head><title>Site | Some paper</title>
    <meta name="citation_title" content="Some Paper: A Study">
    <meta name="citation_pdf_url" content="/papers/1.pdf"></head><body><a href="x.pdf">other</a></body></html>"""
    res = parse_page(page, "https://example.org/forum?id=1")
    assert res.title == "Some Paper: A Study"
    assert res.pdf_url == "https://example.org/papers/1.pdf"


def test_parse_page_arxiv_link_gives_pdf():
    page = b"""<html><head><title>Project page</title></head>
    <body><a href="https://arxiv.org/abs/2401.12345v2">arXiv</a></body></html>"""
    res = parse_page(page, "https://proj.example.org/")
    assert res.arxiv_id == "2401.12345"
    assert res.pdf_url == "https://arxiv.org/pdf/2401.12345"


def test_parse_page_relative_pdf_and_no_pdf():
    res = parse_page(b'<html><body><a href="paper.pdf">Download</a></body></html>', "https://a.b/c/d")
    assert res.pdf_url == "https://a.b/c/paper.pdf"
    assert parse_page(b"<html><title>T</title></html>", "https://a.b").pdf_url is None


def test_scholar_inbox_slug():
    words = scholar_inbox_slug_words("https://www.scholar-inbox.com/paper/Fu2026ARXIV_DeCAL_Towards_Physically_Grounded")
    assert words == ["decal", "towards", "physically", "grounded"]
    assert scholar_inbox_slug_words("https://www.scholar-inbox.com/paper/Zhang2026_Make_Geometry_Matter_for") == ["make", "geometry", "matter", "for"]
    assert scholar_inbox_slug_words("https://www.scholar-inbox.com/paper/Foo2026CVPR_Some_Title") == ["some", "title"]
    assert scholar_inbox_slug_words("https://www.scholar-inbox.com/paper/") is None
    assert scholar_inbox_slug_words("https://example.com/paper/Fu2026ARXIV_X") is None


def test_parse_arxiv_entries_ids():
    assert parse_arxiv_entries(ATOM) == [("1706.03762", "Attention Is All You Need")]


ABS_PAGE = b"""<html><head><title>[2609.12552] RelateAnything: Real-Time Open-Vocabulary Relation Prediction From Any Inputs</title>
<meta name="citation_title" content="RelateAnything: Real-Time Open-Vocabulary Relation Prediction From Any Inputs"></head><body></body></html>"""


def test_resolve_arxiv_api_down_keeps_pdf_and_uses_abs_page(monkeypatch):
    from paperqueue import links

    def fake_get(url):
        if "export.arxiv.org" in url:
            raise OSError("HTTP 429")
        return ABS_PAGE

    monkeypatch.setattr(links, "_get", fake_get)
    res = links.resolve("https://arxiv.org/abs/2609.12552", "2609.12552")
    assert res.pdf_url == "https://arxiv.org/pdf/2609.12552"
    assert res.title == "RelateAnything: Real-Time Open-Vocabulary Relation Prediction From Any Inputs"
    assert res.arxiv_id == "2609.12552"


def test_resolve_arxiv_all_network_down_keeps_pdf(monkeypatch):
    from paperqueue import links

    def fake_get(url):
        raise OSError("down")

    monkeypatch.setattr(links, "_get", fake_get)
    res = links.resolve("https://arxiv.org/abs/2609.12552", "2609.12552")
    assert res.pdf_url == "https://arxiv.org/pdf/2609.12552"
    assert res.title is None
