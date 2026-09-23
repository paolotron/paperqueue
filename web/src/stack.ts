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
const RETRY_MS = 10 * 60 * 1000;

const now = () => new Date().toISOString().replace(/\.\d{3}Z$/, "+00:00");

function needsResolve(p: Paper): boolean {
  return !p.pdf_url || p.title === p.url;
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
  };
}

export class PaperStack extends DurableObject<Env> {
  private state: State | null = null;
  private attempts = new Map<string, number>();

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

  private async resolveOne(url: string, arxivId: string | null): Promise<void> {
    const res = await resolve(url, arxivId);
    await this.applyResolved(url, res);
  }

  async applyResolved(url: string, res: Resolved): Promise<boolean> {
    if (!res.title && !res.pdf_url && !res.arxiv_id) return false;
    const state = await this.load();
    const paper = state.papers.find((p) => p.url === url) ?? state.history.find((p) => p.url === url);
    if (!paper) return false;
    if (res.title) paper.title = res.title;
    if (res.pdf_url) paper.pdf_url = res.pdf_url;
    if (res.arxiv_id) paper.arxiv_id = res.arxiv_id;
    await this.commit(state);
    return true;
  }

  // -- reads -------------------------------------------------------------

  async getState(): Promise<State> {
    const state = await this.load();
    for (const p of state.papers) if (needsResolve(p)) this.schedule(p);
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
