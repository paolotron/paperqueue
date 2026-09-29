// The shared paper stack: one Durable Object instance holds the whole state.
import { DurableObject } from "cloudflare:workers";
import { resolve, type Resolved } from "./links";

export interface Paper {
  url: string;
  title: string;
  added_at: string;
  arxiv_id: string | null;
  pdf_url: string | null;
  read_at: string | null;
  review_comment: string | null;
  usefulness: number | null;
  conference: string | null;
  /** Venue detection has run for this paper (or the reader set it); the backfill skips it. */
  conference_checked: boolean;
}

/** Same shape as the TUI's stack.json: papers bottom->top, history oldest->newest. */
export interface State {
  version: number;
  papers: Paper[];
  history: Paper[];
}

export interface Mutation {
  paper: Paper | null;
  state: State;
}

export interface Review {
  comment: string;
  usefulness: number;
}

const MAX_HISTORY = 500;
const MAX_CONFERENCE = 80;
const RETRY_MS = 10 * 60 * 1000;
// Papers from before the Conference column get one conference-only lookup each, spaced
// out so a big history does not trip the arXiv API's rate limit (one call per 3 s).
const BACKFILL_GAP_MS = 4000;

const now = () => new Date().toISOString().replace(/\.\d{3}Z$/, "+00:00");

function needsResolve(p: Paper): boolean {
  return !p.pdf_url || p.title === p.url;
}

function needsConference(p: Paper): boolean {
  return !p.conference && !p.conference_checked;
}

function cleanConference(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value.trim().slice(0, MAX_CONFERENCE) : null;
}

function coerce(raw: Partial<Paper> & { url: string }): Paper {
  return {
    url: raw.url,
    title: raw.title || raw.url,
    added_at: raw.added_at || now(),
    arxiv_id: raw.arxiv_id ?? null,
    pdf_url: raw.pdf_url ?? null,
    read_at: raw.read_at ?? null,
    review_comment: typeof raw.review_comment === "string" && raw.review_comment.trim() ? raw.review_comment.trim() : null,
    usefulness: typeof raw.usefulness === "number" && Number.isFinite(raw.usefulness) ? raw.usefulness : null,
    conference: cleanConference(raw.conference),
    conference_checked: raw.conference_checked === true,
  };
}

export class PaperStack extends DurableObject<Env> {
  private state: State | null = null;
  private attempts = new Map<string, number>();
  private lastBackfill = 0;

  private async load(): Promise<State> {
    if (!this.state) {
      this.state = (await this.ctx.storage.get<State>("state")) ?? { version: 0, papers: [], history: [] };
    }
    return this.state;
  }

  private async commit(state: State): Promise<State> {
    const next = { ...state, version: state.version + 1 };
    try {
      await this.ctx.storage.put("state", next);
    } catch (err) {
      this.state = null; // reload from storage next time rather than trusting dirty memory
      throw err;
    }
    this.state = next;
    return next;
  }

  private archive(state: State, paper: Paper, review?: Review): void {
    paper.read_at = now();
    if (review) {
      paper.review_comment = review.comment;
      paper.usefulness = review.usefulness;
    }
    state.history = state.history.filter((h) => h.url !== paper.url);
    state.history.push(paper);
    if (state.history.length > MAX_HISTORY) state.history.splice(0, state.history.length - MAX_HISTORY);
  }

  // -- resolution --------------------------------------------------------

  private schedule(paper: Paper): void {
    const last = this.attempts.get(paper.url) ?? 0;
    if (Date.now() - last < RETRY_MS) return;
    this.attempts.set(paper.url, Date.now());
    this.ctx.waitUntil(this.resolveOne(paper.url, paper.arxiv_id));
  }

  /** Start the next conference-only lookup, at most one per BACKFILL_GAP_MS. */
  private backfill(state: State): void {
    if (Date.now() - this.lastBackfill < BACKFILL_GAP_MS) return;
    const cutoff = Date.now() - RETRY_MS;
    // Queued papers still missing a title or PDF get the full resolution, which detects the venue too.
    const next = [...state.papers.filter((p) => !needsResolve(p)), ...state.history].find(
      (p) => needsConference(p) && (this.attempts.get(p.url) ?? 0) < cutoff,
    );
    if (!next) return;
    this.lastBackfill = Date.now();
    this.attempts.set(next.url, Date.now());
    this.ctx.waitUntil(this.resolveOne(next.url, next.arxiv_id, true));
  }

  private async resolveOne(url: string, arxivId: string | null, conferenceOnly = false): Promise<void> {
    const res = await resolve(url, arxivId);
    await this.applyResolved(url, res, conferenceOnly);
  }

  /**
   * Store what resolution found. conferenceOnly (the backfill) leaves title, PDF and arXiv id alone.
   * A lookup that answered (a title or a venue came back) marks the conference as checked; a
   * throttled or failed one leaves it for a retry.
   */
  async applyResolved(url: string, res: Resolved, conferenceOnly = false): Promise<boolean> {
    const answered = !!(res.title || res.conference);
    if (!answered && (conferenceOnly || (!res.pdf_url && !res.arxiv_id))) return false;
    const state = await this.load();
    const paper = state.papers.find((p) => p.url === url) ?? state.history.find((p) => p.url === url);
    if (!paper) return false;
    if (!conferenceOnly) {
      if (res.title) paper.title = res.title;
      if (res.pdf_url) paper.pdf_url = res.pdf_url;
      if (res.arxiv_id) paper.arxiv_id = res.arxiv_id;
    }
    // A conference the reader typed in wins over whatever the resolver guesses.
    if (res.conference && !paper.conference) paper.conference = res.conference;
    if (answered) paper.conference_checked = true;
    await this.commit(state);
    return true;
  }

  // -- reads -------------------------------------------------------------

  async getState(): Promise<State> {
    const state = await this.load();
    for (const p of state.papers) if (needsResolve(p)) this.schedule(p);
    this.backfill(state);
    return state;
  }

  // -- mutations ---------------------------------------------------------

  /** Push an already-normalized URL. ok=false when it is already queued. */
  async push(url: string, arxivId: string | null, doResolve = true): Promise<Mutation & { ok: boolean }> {
    const state = await this.load();
    const existing = state.papers.find((p) => p.url === url);
    if (existing) return { ok: false, paper: existing, state };
    const paper = coerce({ url, arxiv_id: arxivId });
    state.papers.push(paper);
    const next = await this.commit(state);
    if (doResolve) this.schedule(paper);
    return { ok: true, paper, state: next };
  }

  async pop(review: Review, expectedUrl?: string): Promise<Mutation> {
    const state = await this.load();
    if (expectedUrl && state.papers.at(-1)?.url !== expectedUrl) return { paper: null, state };
    const paper = state.papers.pop();
    if (!paper) return { paper: null, state };
    this.archive(state, paper, review);
    return { paper, state: await this.commit(state) };
  }

  async takeBottom(review: Review, expectedUrl?: string): Promise<Mutation> {
    const state = await this.load();
    if (expectedUrl && state.papers[0]?.url !== expectedUrl) return { paper: null, state };
    const paper = state.papers.shift();
    if (!paper) return { paper: null, state };
    this.archive(state, paper, review);
    return { paper, state: await this.commit(state) };
  }

  async remove(url: string): Promise<Mutation> {
    const state = await this.load();
    const idx = state.papers.findIndex((p) => p.url === url);
    if (idx < 0) return { paper: null, state };
    const [paper] = state.papers.splice(idx, 1);
    this.archive(state, paper);
    return { paper, state: await this.commit(state) };
  }

  /** Move a paper from history back onto the top of the stack. */
  async restore(url: string): Promise<Mutation> {
    const state = await this.load();
    const idx = state.history.findIndex((p) => p.url === url);
    if (idx < 0) return { paper: null, state };
    const [paper] = state.history.splice(idx, 1);
    if (!state.papers.some((p) => p.url === url)) {
      paper.read_at = null;
      paper.added_at = now();
      paper.review_comment = null;
      paper.usefulness = null;
      state.papers.push(paper);
    }
    return { paper, state: await this.commit(state) };
  }

  /** Set (or clear, with an empty string) the conference of a queued or read paper. */
  async setConference(url: string, conference: string): Promise<Mutation> {
    const state = await this.load();
    const paper = state.papers.find((p) => p.url === url) ?? state.history.find((p) => p.url === url);
    if (!paper) return { paper: null, state };
    paper.conference = cleanConference(conference);
    paper.conference_checked = true;
    return { paper, state: await this.commit(state) };
  }

  async forget(url: string): Promise<Mutation> {
    const state = await this.load();
    const idx = state.history.findIndex((p) => p.url === url);
    if (idx < 0) return { paper: null, state };
    const [paper] = state.history.splice(idx, 1);
    return { paper, state: await this.commit(state) };
  }

  /** Merge a client's local file: unknown papers are appended, known ones are left as the server has them. */
  async importState(
    incoming: { papers?: Array<Partial<Paper> & { url: string }>; history?: Array<Partial<Paper> & { url: string }> },
    doResolve = true,
  ): Promise<{ added_papers: number; added_history: number; state: State }> {
    const state = await this.load();
    const known = new Set([...state.papers, ...state.history].map((p) => p.url));
    let addedPapers = 0;
    let addedHistory = 0;
    for (const raw of incoming.papers ?? []) {
      if (!raw?.url || known.has(raw.url)) continue;
      const paper = coerce(raw);
      paper.read_at = null;
      state.papers.push(paper);
      known.add(paper.url);
      addedPapers++;
      if (doResolve && needsResolve(paper)) this.schedule(paper);
    }
    for (const raw of incoming.history ?? []) {
      if (!raw?.url || known.has(raw.url)) continue;
      const paper = coerce(raw);
      paper.read_at = paper.read_at ?? now();
      state.history.push(paper);
      known.add(paper.url);
      addedHistory++;
    }
    if (state.history.length > MAX_HISTORY) state.history.splice(0, state.history.length - MAX_HISTORY);
    const next = addedPapers || addedHistory ? await this.commit(state) : state;
    return { added_papers: addedPapers, added_history: addedHistory, state: next };
  }
}
