import { afterEach, describe, expect, it, vi } from "vitest";
import { normalize, openreviewPdf, parseArxivEntries, parsePage, resolve, scholarInboxSlugWords, venueFromText, venueFromUrl } from "../src/links";

const ABS = "https://arxiv.org/abs/1706.03762";

describe("normalize", () => {
  it.each([
    ["https://arxiv.org/abs/1706.03762", ABS, "1706.03762"],
    ["https://arxiv.org/abs/1706.03762v7", ABS, "1706.03762"],
    ["https://arxiv.org/pdf/1706.03762", ABS, "1706.03762"],
    ["https://arxiv.org/pdf/1706.03762v2.pdf", ABS, "1706.03762"],
    ["http://www.arxiv.org/html/1706.03762v1/", ABS, "1706.03762"],
    ["arxiv.org/abs/1706.03762", ABS, "1706.03762"],
    ["  1706.03762  ", ABS, "1706.03762"],
    ["arXiv:1706.03762v3", ABS, "1706.03762"],
    ["https://www.alphaxiv.org/abs/1706.03762", ABS, "1706.03762"],
    ["https://arxiv.org/abs/hep-th/9901001", "https://arxiv.org/abs/hep-th/9901001", "hep-th/9901001"],
    ["https://openreview.net/forum?id=abc", "https://openreview.net/forum?id=abc", null],
    ["openreview.net/forum?id=abc", "https://openreview.net/forum?id=abc", null],
    ["https://arxiv.org/list/cs.AI/recent", "https://arxiv.org/list/cs.AI/recent", null],
  ])("%s", (raw, url, arxivId) => {
    expect(normalize(raw)).toEqual({ url, arxivId });
  });

  it.each(["", "   ", "not a link"])("rejects %j", (raw) => {
    expect(() => normalize(raw)).toThrow();
  });
});

const ATOM = `<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title type="html">ArXiv Query</title>
  <entry>
    <id>http://arxiv.org/abs/1706.03762v7</id>
    <title>Attention Is All
  You Need</title>
  </entry>
</feed>`;

describe("arXiv atom", () => {
  it("parses entries", () => {
    expect(parseArxivEntries(ATOM)).toEqual([["1706.03762", "Attention Is All You Need", null]]);
  });
  it("ignores error entries", () => {
    expect(parseArxivEntries('<feed><entry><id>http://arxiv.org/api/errors#x</id><title>Error</title></entry></feed>')).toEqual([]);
  });
});

const ECCV = `<html><head><title>ECCV Poster JSON: Jigsaw Self-play Optimization for Normalizing Flows</title></head>
<body><h1><a href="#">CSP Test</a></h1><h1 class="event-title">JSON: Jigsaw Self-play Optimization for Normalizing Flows</h1>
<a href="/virtual/2026/papers.html">All papers</a>
<a class="action-btn" href="https://media.eventhosts.cc/Conferences/ECCV2026/pdfs/2253.pdf" target="_blank">
  <i class="fas fa-file-pdf"></i> Paper PDF</a></body></html>`;

describe("parsePage", () => {
  it("ECCV poster page", async () => {
    const res = await parsePage(ECCV, "https://eccv.ecva.net/virtual/2026/poster/3623");
    expect(res).toEqual({
      title: "JSON: Jigsaw Self-play Optimization for Normalizing Flows",
      pdf_url: "https://media.eventhosts.cc/Conferences/ECCV2026/pdfs/2253.pdf",
      arxiv_id: null,
      conference: null,
    });
  });

  it("citation metadata wins", async () => {
    const page = `<html><head><title>Site | Some paper</title>
    <meta name="citation_title" content="Some Paper: A Study">
    <meta name="citation_pdf_url" content="/papers/1.pdf"></head><body><a href="x.pdf">other</a></body></html>`;
    const res = await parsePage(page, "https://example.org/forum?id=1");
    expect(res.title).toBe("Some Paper: A Study");
    expect(res.pdf_url).toBe("https://example.org/papers/1.pdf");
  });

  it("arXiv link gives id and pdf", async () => {
    const page = `<html><head><title>Project page</title></head>
    <body><a href="https://arxiv.org/abs/2401.12345v2">arXiv</a></body></html>`;
    const res = await parsePage(page, "https://proj.example.org/");
    expect(res.arxiv_id).toBe("2401.12345");
    expect(res.pdf_url).toBe("https://arxiv.org/pdf/2401.12345");
  });

  it("relative pdf, bot walls, entities", async () => {
    expect((await parsePage('<html><body><a href="paper.pdf">Download</a></body></html>', "https://a.b/c/d")).pdf_url).toBe(
      "https://a.b/c/paper.pdf",
    );
    expect((await parsePage("<html><title>T</title></html>", "https://a.b")).pdf_url).toBeNull();
    expect((await parsePage("<title>Verifying your browser | OpenReview</title>", "https://a.b")).title).toBeNull();
    expect((await parsePage("<title>\n Some &amp; Paper \n</title>", "https://a.b")).title).toBe("Some & Paper");
  });
});

describe("site rules", () => {
  it("scholar inbox slugs", () => {
    expect(scholarInboxSlugWords("https://www.scholar-inbox.com/paper/Fu2026ARXIV_DeCAL_Towards_Physically_Grounded")).toEqual([
      "decal", "towards", "physically", "grounded",
    ]);
    expect(scholarInboxSlugWords("https://www.scholar-inbox.com/paper/Zhang2026_Make_Geometry_Matter_for")).toEqual([
      "make", "geometry", "matter", "for",
    ]);
    expect(scholarInboxSlugWords("https://www.scholar-inbox.com/paper/")).toBeNull();
    expect(scholarInboxSlugWords("https://example.com/paper/Fu2026ARXIV_X")).toBeNull();
  });

  it("openreview pdf", () => {
    expect(openreviewPdf("https://openreview.net/forum?id=bzs4uPLXvi")).toBe("https://openreview.net/pdf?id=bzs4uPLXvi");
    expect(openreviewPdf("https://openreview.net/forum?noteId=1&id=abc-D_9")).toBe("https://openreview.net/pdf?id=abc-D_9");
    expect(openreviewPdf("https://openreview.net/group?id=ICLR")).toBeNull();
    expect(openreviewPdf("https://example.com/forum?id=x")).toBeNull();
  });
});

const ABS_PAGE = `<html><head><title>[2609.12552] RelateAnything: Real-Time Open-Vocabulary Relation Prediction From Any Inputs</title>
<meta name="citation_title" content="RelateAnything: Real-Time Open-Vocabulary Relation Prediction From Any Inputs"></head><body></body></html>`;

describe("resolve with the arXiv API down", () => {
  function stubFetch(handler: (url: string) => Response | Promise<Response>) {
    vi.stubGlobal("fetch", (input: RequestInfo | URL) => Promise.resolve(handler(String(input))));
  }
  afterEach(() => vi.unstubAllGlobals());

  it("keeps the PDF and falls back to the abs page title on 429", async () => {
    stubFetch((url) =>
      url.includes("export.arxiv.org") ? new Response("throttled", { status: 429 }) : new Response(ABS_PAGE),
    );
    expect(await resolve("https://arxiv.org/abs/2609.12552", "2609.12552")).toEqual({
      title: "RelateAnything: Real-Time Open-Vocabulary Relation Prediction From Any Inputs",
      pdf_url: "https://arxiv.org/pdf/2609.12552",
      arxiv_id: "2609.12552",
      conference: null,
    });
  });

  it("keeps the PDF when every fetch fails", async () => {
    stubFetch(() => {
      throw new Error("network down");
    });
    expect(await resolve("https://arxiv.org/abs/2609.12552", "2609.12552")).toEqual({
      title: null,
      pdf_url: "https://arxiv.org/pdf/2609.12552",
      arxiv_id: "2609.12552",
      conference: null,
    });
  });
});

const ATOM_VENUE = `<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry><id>http://arxiv.org/abs/2501.00001v1</id><title>A Paper</title>
  <arxiv:comment>Accepted at CVPR 2025. Code: https://x.y</arxiv:comment></entry>
  <entry><id>http://arxiv.org/abs/2501.00002v1</id><title>Another Paper</title>
  <arxiv:journal_ref>NeurIPS'23</arxiv:journal_ref><arxiv:comment>ICML 2022 workshop</arxiv:comment></entry>
</feed>`;

describe("venues", () => {
  it("reads journal_ref, then comment, from arXiv entries", () => {
    expect(parseArxivEntries(ATOM_VENUE)).toEqual([
      ["2501.00001", "A Paper", "CVPR 2025"],
      ["2501.00002", "Another Paper", "NeurIPS 2023"],
    ]);
  });

  it.each([
    ["Accepted at CVPR 2025", "CVPR 2025"],
    ["NeurIPS'23", "NeurIPS 2023"],
    ["ECCV2026", "ECCV 2026"],
    ["In ICLR, 2024", "ICLR 2024"],
    ["NIPS 2017", "NeurIPS 2017"],
    ["SIGGRAPH Asia 2023", "SIGGRAPH Asia 2023"],
    ["Proceedings of NAACL", "NAACL"],
    ["a chi-squared test", null],
    ["", null],
  ])("text %j", (text, expected) => {
    expect(venueFromText(text)).toBe(expected);
  });

  it.each([
    ["https://media.eventhosts.cc/Conferences/ECCV2026/pdfs/2253.pdf", "ECCV 2026"],
    ["https://openaccess.thecvf.com/content/CVPR2024/html/X_CVPR_2024_paper.html", "CVPR 2024"],
    ["https://proceedings.neurips.cc/paper_files/paper/2023/hash/abc-Abstract-Conference.html", "NeurIPS 2023"],
    ["https://aclanthology.org/2024.acl-long.12/", "ACL 2024"],
    ["https://www.scholar-inbox.com/paper/Foo2026CVPR_Some_Title", "CVPR 2026"],
    ["https://www.scholar-inbox.com/paper/Fu2026ARXIV_DeCAL", null],
    ["https://eccv.ecva.net/virtual/2026/poster/3623", "ECCV 2026"],
    ["https://neurips.cc/virtual/2023/poster/1", "NeurIPS 2023"],
    ["https://www.example.org/virtual/2023/poster/1", null],
    ["https://example.com/paper.pdf", null],
  ])("url %s", (url, expected) => {
    expect(venueFromUrl(url)).toBe(expected);
  });

  it("citation conference meta", async () => {
    const page = `<html><head><meta name="citation_title" content="T T T">
    <meta name="citation_conference_title" content="Proceedings of the IEEE/CVF Conference (CVPR 2024)"></head></html>`;
    expect((await parsePage(page, "https://a.b")).conference).toBe("CVPR 2024");
    const other = '<html><head><meta name="citation_conference_title" content="Some Workshop"></head></html>';
    expect((await parsePage(other, "https://a.b")).conference).toBe("Some Workshop");
    expect((await parsePage("<html><title>T</title></html>", "https://a.b")).conference).toBeNull();
  });
});
