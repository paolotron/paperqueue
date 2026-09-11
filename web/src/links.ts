// Port of src/paperqueue/links.py: URL normalization, title lookup, PDF resolution.

export interface Resolved {
  title: string | null;
  pdf_url: string | null;
  arxiv_id: string | null;
}

const USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) paperqueue/0.1";
const TIMEOUT_MS = 8000;
const MAX_HTML = 512 * 1024;

// New-style ids (2007+): YYMM.NNNNN[vN]; old-style: archive/NNNNNNN[vN]
const ARXIV_ID = String.raw`(?:\d{4}\.\d{4,5}|[a-z\-]+(?:\.[A-Z]{2})?/\d{7})(?:v\d+)?`;
const BARE_ID_RE = new RegExp(`^(?:arxiv:)?(${ARXIV_ID})$`, "i");
const PATH_ID_RE = new RegExp(String.raw`^/(?:abs|pdf|html)/(${ARXIV_ID})(?:\.pdf)?/?$`, "i");
const HREF_ID_RE = new RegExp(String.raw`arxiv\.org/(?:abs|pdf|html)/(${ARXIV_ID})`, "i");
const ARXIV_HOSTS = new Set(["arxiv.org", "www.arxiv.org", "export.arxiv.org", "alphaxiv.org", "www.alphaxiv.org"]);
const SCHOLAR_INBOX_HOSTS = new Set(["scholar-inbox.com", "www.scholar-inbox.com"]);
const OPENREVIEW_HOSTS = new Set(["openreview.net", "www.openreview.net"]);
const BOT_WALL_RE = /just a moment|verifying your browser|attention required|access denied/i;
const STOP_WORDS = new Set(["a", "an", "and", "as", "at", "by", "for", "from", "in", "is", "of", "on", "or", "the", "to", "via", "with"]);

// -- normalization ---------------------------------------------------------

function stripVersion(id: string): string {
  return id.replace(/v\d+$/, "");
}

export function arxivAbs(id: string): string {
  return `https://arxiv.org/abs/${id}`;
}

export function arxivPdf(id: string): string {
  return `https://arxiv.org/pdf/${id}`;
}

/** Canonical URL plus arXiv id (null for non-arXiv links). Throws on junk input. */
export function normalize(raw: string): { url: string; arxivId: string | null } {
  const text = raw.trim();
  if (!text) throw new Error("empty link");

  let m = BARE_ID_RE.exec(text);
  if (m) {
    const id = stripVersion(m[1]);
    return { url: arxivAbs(id), arxivId: id };
  }

  const candidate = text.includes("://") ? text : `https://${text}`;
  let parsed: URL;
  try {
    parsed = new URL(candidate);
  } catch {
    throw new Error(`not a link: ${raw}`);
  }
  if (ARXIV_HOSTS.has(parsed.hostname.toLowerCase())) {
    m = PATH_ID_RE.exec(parsed.pathname);
    if (m) {
      const id = stripVersion(m[1]);
      return { url: arxivAbs(id), arxivId: id };
    }
  }
  const host = parsed.hostname;
  if (!host || /\s/.test(text) || (!host.includes(".") && host !== "localhost")) {
    throw new Error(`not a link: ${raw}`);
  }
  return { url: candidate, arxivId: null };
}

// -- helpers ---------------------------------------------------------------

const ENTITIES: Record<string, string> = { amp: "&", lt: "<", gt: ">", quot: '"', apos: "'", nbsp: " " };

function unescapeHtml(text: string): string {
  return text.replace(/&(#x[0-9a-f]+|#\d+|[a-z]+);/gi, (whole, ent: string) => {
    if (ent[0] === "#") {
      const code = ent[1].toLowerCase() === "x" ? parseInt(ent.slice(2), 16) : parseInt(ent.slice(1), 10);
      return Number.isFinite(code) ? String.fromCodePoint(code) : whole;
    }
    return ENTITIES[ent.toLowerCase()] ?? whole;
  });
}

function clean(text: string): string {
  return unescapeHtml(text).replace(/\s+/g, " ").trim();
}

function titleWords(text: string): string[] {
  return (text.match(/[A-Za-z0-9]+/g) ?? []).map((w) => w.toLowerCase());
}

async function get(url: string): Promise<string> {
  const resp = await fetch(url, {
    headers: { "User-Agent": USER_AGENT, Accept: "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8" },
    signal: AbortSignal.timeout(TIMEOUT_MS),
    redirect: "follow",
  });
  if (!resp.ok) throw new Error(`HTTP ${resp.status} for ${url}`);
  const body = await resp.text();
  return body.length > MAX_HTML ? body.slice(0, MAX_HTML) : body;
}

// -- arXiv API -------------------------------------------------------------

/** [[arxivId, title], ...] from an arXiv API Atom response. */
export function parseArxivEntries(xml: string): Array<[string, string]> {
  const out: Array<[string, string]> = [];
  for (const entry of xml.matchAll(/<entry>([\s\S]*?)<\/entry>/g)) {
    const body = entry[1];
    const t = /<title[^>]*>([\s\S]*?)<\/title>/.exec(body);
    const i = /<id>([\s\S]*?)<\/id>/.exec(body);
    const title = t ? clean(t[1]) : "";
    const m = i ? HREF_ID_RE.exec(i[1]) : null;
    // The API returns a single entry titled "Error" for unknown ids.
    if (title && title !== "Error" && m) out.push([stripVersion(m[1]), title]);
  }
  return out;
}

async function arxivLookup(id: string): Promise<string | null> {
  const entries = parseArxivEntries(await get(`https://export.arxiv.org/api/query?id_list=${encodeURIComponent(id)}`));
  return entries.length ? entries[0][1] : null;
}

/** Find an arXiv paper whose title contains every word, in order. Phrase first, then AND of content words. */
export async function arxivSearchTitle(words: string[]): Promise<[string, string] | null> {
  words = words.filter(Boolean);
  if (!words.length) return null;
  const phrase = encodeURIComponent(words.slice(0, 10).join(" "));
  const content = words.filter((w) => !STOP_WORDS.has(w) && w.length > 1).slice(0, 8);
  const queries = [`ti:%22${phrase}%22`];
  if (content.length) queries.push(content.map((w) => `ti:${encodeURIComponent(w)}`).join("+AND+"));
  const wanted = words.join(" ");
  for (const query of queries) {
    const xml = await get(`https://export.arxiv.org/api/query?search_query=${query}&max_results=5`);
    for (const [id, title] of parseArxivEntries(xml)) {
      if (titleWords(title).join(" ").includes(wanted)) return [id, title];
    }
  }
  return null;
}

// -- HTML page parsing -----------------------------------------------------

interface PageBits {
  title: string;
  h1s: string[];
  meta: Map<string, string>;
  links: Array<[string, string]>;
}

async function scrape(html: string): Promise<PageBits> {
  const p: PageBits = { title: "", h1s: [], meta: new Map(), links: [] };
  let titleBuf = "";
  let h1Buf = "";
  let aBuf = "";
  let aHref = "";
  const rewriter = new HTMLRewriter()
    .on("title", {
      element(el) {
        titleBuf = "";
        el.onEndTag(() => {
          if (!p.title) p.title = clean(titleBuf);
        });
      },
      text(t) {
        titleBuf += t.text;
      },
    })
    .on("h1", {
      element(el) {
        h1Buf = "";
        el.onEndTag(() => {
          const s = clean(h1Buf);
          if (s) p.h1s.push(s);
        });
      },
      text(t) {
        h1Buf += t.text;
      },
    })
    .on("meta", {
      element(el) {
        const key = (el.getAttribute("name") || el.getAttribute("property") || "").toLowerCase();
        const content = el.getAttribute("content");
        if (key && content !== null && !p.meta.has(key)) p.meta.set(key, clean(content));
      },
    })
    .on("a[href]", {
      element(el) {
        aHref = el.getAttribute("href") ?? "";
        aBuf = "";
        const href = aHref;
        el.onEndTag(() => {
          p.links.push([href, clean(aBuf)]);
        });
      },
      text(t) {
        aBuf += t.text;
      },
    });
  await rewriter.transform(new Response(html)).text();
  return p;
}

function isPdfHref(href: string): boolean {
  let path: string;
  try {
    path = new URL(href, "https://x.invalid/").pathname.toLowerCase();
  } catch {
    return false;
  }
  return path.endsWith(".pdf") || path.includes("/pdf/");
}

function join(base: string, href: string): string {
  try {
    return new URL(href, base || undefined).toString();
  } catch {
    return href;
  }
}

/** Extract title, PDF link and arXiv id from an HTML page (no network). */
export async function parsePage(html: string, baseUrl: string): Promise<Resolved> {
  const p = await scrape(html);
  const res: Resolved = { title: null, pdf_url: null, arxiv_id: null };

  // Title: citation metadata, then a heading that the <title> repeats
  // (conference sites prefix the title, e.g. "ECCV Poster <title>"), then og:title, then <title>.
  const candidates: Array<string | undefined> = [p.meta.get("citation_title"), p.meta.get("dc.title")];
  if (p.title) {
    candidates.push(...[...p.h1s].sort((a, b) => b.length - a.length).filter((h) => p.title.includes(h)));
  }
  candidates.push(p.meta.get("og:title"), p.title);
  for (const cand of candidates) {
    if (cand && !BOT_WALL_RE.test(cand)) {
      res.title = cand;
      break;
    }
  }

  // arXiv id from any arxiv.org/abs|pdf link or citation metadata.
  for (const [href] of p.links) {
    const m = HREF_ID_RE.exec(href);
    if (m) {
      res.arxiv_id = stripVersion(m[1]);
      break;
    }
  }
  if (!res.arxiv_id) {
    const m = BARE_ID_RE.exec(p.meta.get("citation_arxiv_id") ?? "");
    if (m) res.arxiv_id = stripVersion(m[1]);
  }

  // PDF: citation_pdf_url, then a link whose text mentions PDF, then any .pdf link.
  let pdf = p.meta.get("citation_pdf_url");
  if (!pdf) {
    const pdfLinks = p.links.filter(([h]) => isPdfHref(h));
    const preferred = pdfLinks.filter(([, t]) => t.toLowerCase().includes("pdf"));
    pdf = preferred[0]?.[0] ?? pdfLinks[0]?.[0];
  }
  if (pdf) res.pdf_url = join(baseUrl, pdf);
  else if (res.arxiv_id) res.pdf_url = arxivPdf(res.arxiv_id);
  return res;
}

// -- site-specific ---------------------------------------------------------

/** Slugs look like '/paper/<Author><Year>[<VENUE>]_<Title_words>'; return the title words. */
export function scholarInboxSlugWords(url: string): string[] | null {
  let parsed: URL;
  try {
    parsed = new URL(url);
  } catch {
    return null;
  }
  if (!SCHOLAR_INBOX_HOSTS.has(parsed.hostname.toLowerCase())) return null;
  const m = /^\/paper\/[^/_]*\d{4}[^/_]*_(.+?)\/?$/.exec(parsed.pathname);
  if (!m) return null;
  const words = titleWords(m[1]);
  return words.length ? words : null;
}

/** openreview.net/forum?id=X -> openreview.net/pdf?id=X (the site blocks scripted fetches). */
export function openreviewPdf(url: string): string | null {
  let parsed: URL;
  try {
    parsed = new URL(url);
  } catch {
    return null;
  }
  if (!OPENREVIEW_HOSTS.has(parsed.hostname.toLowerCase())) return null;
  const id = parsed.searchParams.get("id");
  if (!id || !/^[A-Za-z0-9_-]+$/.test(id)) return null;
  if (!["/forum", "/pdf", "/attachment"].includes(parsed.pathname)) return null;
  return `https://openreview.net/pdf?id=${id}`;
}

function looksGeneric(title: string): boolean {
  return titleWords(title).length < 3;
}

// -- entry point -----------------------------------------------------------

/** Best-effort title / PDF / arXiv id for a link. Never throws. */
export async function resolve(url: string, arxivId: string | null = null): Promise<Resolved> {
  let res: Resolved = { title: null, pdf_url: null, arxiv_id: arxivId };
  try {
    if (arxivId) {
      res.title = await arxivLookup(arxivId);
      res.pdf_url = arxivPdf(arxivId);
      return res;
    }

    const words = scholarInboxSlugWords(url);
    if (words) {
      const hit = await arxivSearchTitle(words);
      if (hit) return { arxiv_id: hit[0], title: hit[1], pdf_url: arxivPdf(hit[0]) };
    }

    const orPdf = openreviewPdf(url);
    if (orPdf) res.pdf_url = orPdf;

    const page = await parsePage(await get(url), url);
    res = { title: page.title, pdf_url: page.pdf_url ?? res.pdf_url, arxiv_id: page.arxiv_id };
    if (res.arxiv_id && (!res.title || looksGeneric(res.title))) {
      res.title = (await arxivLookup(res.arxiv_id)) ?? res.title;
    }
    return res;
  } catch {
    return res;
  }
}
