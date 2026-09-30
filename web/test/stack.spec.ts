import { env, SELF } from "cloudflare:test";
import { describe, expect, it } from "vitest";

const fresh = () => env.STACK.getByName(`test-${crypto.randomUUID()}`);
const urls = (state: { papers: { url: string }[] }) => state.papers.map((p) => p.url);
const hist = (state: { history: { url: string }[] }) => state.history.map((p) => p.url);
const review = { comment: "Clear and useful", usefulness: 3.7 };

describe("PaperStack", () => {
  it("pop is LIFO, take is FIFO, both archive", async () => {
    const s = fresh();
    for (const u of ["a", "b", "c"]) expect((await s.push(u, null, false)).ok).toBe(true);
    expect((await s.push("a", null, false)).ok).toBe(false);
    expect((await s.pop(review)).paper?.url).toBe("c");
    expect((await s.takeBottom(review)).paper?.url).toBe("a");
    const r = await s.remove("b");
    expect(r.paper?.url).toBe("b");
    expect(urls(r.state)).toEqual([]);
    expect(hist(r.state)).toEqual(["c", "a", "b"]);
    expect(r.state.history.every((p) => p.read_at)).toBe(true);
    expect(r.state.history[0]).toMatchObject({ review_comment: review.comment, usefulness: review.usefulness });
    expect((await s.pop(review)).paper).toBeNull();
    expect((await s.takeBottom(review)).paper).toBeNull();
  });

  it("restore, forget, version bumps, persistence", async () => {
    const s = fresh();
    await s.push("a", null, false);
    const v1 = (await s.getState()).version;
    await s.pop(review);
    const restored = await s.restore("a");
    expect(restored.paper?.read_at).toBeNull();
    expect(restored.paper?.review_comment).toBeNull();
    expect(restored.paper?.usefulness).toBeNull();
    expect(urls(restored.state)).toEqual(["a"]);
    expect(hist(restored.state)).toEqual([]);
    expect(restored.state.version).toBeGreaterThan(v1);
    expect((await s.restore("missing")).paper).toBeNull();
    await s.pop(review);
    expect((await s.forget("a")).paper?.url).toBe("a");
    expect(hist(await s.getState())).toEqual([]);
  });

  it("applyResolved updates papers and history", async () => {
    const s = fresh();
    await s.push("https://x.org/p", null, false);
    expect(await s.applyResolved("https://x.org/p", { title: "T", pdf_url: "https://x.org/p.pdf", arxiv_id: null, conference: null })).toBe(true);
    const p = (await s.getState()).papers[0];
    expect(p.title).toBe("T");
    expect(p.pdf_url).toBe("https://x.org/p.pdf");
    expect(await s.applyResolved("nope", { title: "T", pdf_url: null, arxiv_id: null, conference: null })).toBe(false);
  });

  it("conference: resolution fills it, a manual value wins, history is editable", async () => {
    const s = fresh();
    await s.push("a", null, false);
    const none = { title: null, pdf_url: null, arxiv_id: null };
    expect(await s.applyResolved("a", { ...none, conference: "CVPR 2025" })).toBe(true);
    expect((await s.getState()).papers[0].conference).toBe("CVPR 2025");
    expect((await s.setConference("a", "  ECCV 2026 ")).paper?.conference).toBe("ECCV 2026");
    await s.applyResolved("a", { ...none, conference: "CVPR 2025" });
    expect((await s.getState()).papers[0].conference).toBe("ECCV 2026");
    await s.pop(review);
    expect((await s.setConference("a", "ICML")).paper?.conference).toBe("ICML");
    expect((await s.setConference("a", "   ")).paper?.conference).toBeNull();
    expect((await s.setConference("missing", "ICML")).paper).toBeNull();
    await s.setConference("a", "NeurIPS 2023");
    expect((await s.restore("a")).paper?.conference).toBe("NeurIPS 2023");
  });

  it("conference backfill: conference-only, checked once the lookup answers", async () => {
    const s = fresh();
    await s.push("a", null, false);
    const none = { title: null, pdf_url: null, arxiv_id: null, conference: null };
    await s.applyResolved("a", { ...none, title: "Original", pdf_url: "a.pdf" });
    const paper = async () => (await s.getState()).papers[0];
    expect((await paper()).conference_checked).toBe(true);

    await s.pop(review);
    const hist0 = async () => (await s.getState()).history[0];
    expect(await s.applyResolved("a", { ...none, pdf_url: "x.pdf" }, true)).toBe(false); // throttled: retry later
    expect(await s.applyResolved("a", { ...none, title: "Other", pdf_url: "b.pdf", conference: "CVPR 2025" }, true)).toBe(true);
    expect(await hist0()).toMatchObject({ title: "Original", pdf_url: "a.pdf", conference: "CVPR 2025", conference_checked: true });

    await s.push("b", null, false);
    await s.applyResolved("b", { ...none, pdf_url: "b.pdf", arxiv_id: "2501.00001" });
    expect((await s.getState()).papers[0].conference_checked).toBe(false);
    expect((await s.setConference("b", "")).paper?.conference_checked).toBe(true);
  });

  it("rename: sticks through resolution, empty keeps it, history is editable", async () => {
    const s = fresh();
    await s.push("a", null, false);
    expect((await s.setTitle("a", "  My name  ")).paper?.title).toBe("My name");
    await s.applyResolved("a", { title: "Resolved", pdf_url: "a.pdf", arxiv_id: null, conference: null });
    const p = (await s.getState()).papers[0];
    expect(p.title).toBe("My name");
    expect(p.pdf_url).toBe("a.pdf");
    expect((await s.setTitle("a", "   ")).paper?.title).toBe("My name");
    await s.pop(review);
    expect((await s.setTitle("a", "Later")).paper?.title).toBe("Later");
    expect((await s.setTitle("missing", "x")).paper).toBeNull();
  });

  it("import merges without duplicating", async () => {
    const s = fresh();
    await s.push("a", null, false);
    const r = await s.importState(
      {
        papers: [{ url: "a", title: "ignored" }, { url: "b", title: "B", pdf_url: "https://b/pdf" }],
        history: [{ url: "c", title: "C", read_at: "2026-01-01T00:00:00+00:00" }, { url: "b" }],
      },
      false,
    );
    expect(r.added_papers).toBe(1);
    expect(r.added_history).toBe(1);
    expect(urls(r.state)).toEqual(["a", "b"]);
    expect(r.state.papers[0].title).toBe("a");
    expect(hist(r.state)).toEqual(["c"]);
  });
});

describe("HTTP API", () => {
  const auth = { Authorization: "Bearer test-token", "Content-Type": "application/json" };

  it("rejects missing or wrong tokens", async () => {
    expect((await SELF.fetch("https://pq/api/state")).status).toBe(401);
    expect((await SELF.fetch("https://pq/api/state", { headers: { Authorization: "Bearer nope" } })).status).toBe(401);
  });

  it("round-trips through the routes", async () => {
    const add = await SELF.fetch("https://pq/api/papers", { method: "POST", headers: auth, body: JSON.stringify({ url: "arxiv.org/pdf/1706.03762v2.pdf" }) });
    expect(add.status).toBe(201);
    const added = (await add.json()) as { paper: { url: string; arxiv_id: string } };
    expect(added.paper.url).toBe("https://arxiv.org/abs/1706.03762");
    expect(added.paper.arxiv_id).toBe("1706.03762");

    const dup = await SELF.fetch("https://pq/api/papers", { method: "POST", headers: auth, body: JSON.stringify({ url: "1706.03762" }) });
    expect(dup.status).toBe(409);
    const bad = await SELF.fetch("https://pq/api/papers", { method: "POST", headers: auth, body: JSON.stringify({ url: "not a link" }) });
    expect(bad.status).toBe(400);

    const missingReview = await SELF.fetch("https://pq/api/pop", { method: "POST", headers: auth, body: JSON.stringify({}) });
    expect(missingReview.status).toBe(400);
    const invalidScore = await SELF.fetch("https://pq/api/pop", { method: "POST", headers: auth, body: JSON.stringify({ comment: "x", usefulness: 6 }) });
    expect(invalidScore.status).toBe(400);
    const pop = await SELF.fetch("https://pq/api/pop", { method: "POST", headers: auth, body: JSON.stringify(review) });
    expect(pop.status).toBe(200);
    expect((await SELF.fetch("https://pq/api/pop", { method: "POST", headers: auth, body: JSON.stringify(review) })).status).toBe(404);

    const patchBody = (body: unknown) => ({ method: "PATCH", headers: auth, body: JSON.stringify(body) });
    const conf = await SELF.fetch("https://pq/api/papers", patchBody({ url: "https://arxiv.org/abs/1706.03762", conference: "NeurIPS 2017" }));
    expect(conf.status).toBe(200);
    expect(((await conf.json()) as { paper: { conference: string } }).paper.conference).toBe("NeurIPS 2017");
    expect((await SELF.fetch("https://pq/api/papers", patchBody({ url: "https://arxiv.org/abs/1706.03762" }))).status).toBe(400);
    expect((await SELF.fetch("https://pq/api/papers", patchBody({ url: "nope", conference: "x" }))).status).toBe(404);
    const renamed = await SELF.fetch("https://pq/api/papers", patchBody({ url: "https://arxiv.org/abs/1706.03762", title: "Transformers" }));
    expect(renamed.status).toBe(200);
    expect(((await renamed.json()) as { paper: { title: string } }).paper.title).toBe("Transformers");
    expect((await SELF.fetch("https://pq/api/papers", patchBody({ url: "nope", title: "x" }))).status).toBe(404);

    const restore = await SELF.fetch("https://pq/api/history/restore", { method: "POST", headers: auth, body: JSON.stringify({ url: "https://arxiv.org/abs/1706.03762" }) });
    expect(restore.status).toBe(200);
    const del = await SELF.fetch(`https://pq/api/papers?url=${encodeURIComponent("https://arxiv.org/abs/1706.03762")}`, { method: "DELETE", headers: auth });
    expect(del.status).toBe(200);
    const forget = await SELF.fetch(`https://pq/api/history?url=${encodeURIComponent("https://arxiv.org/abs/1706.03762")}`, { method: "DELETE", headers: auth });
    expect(forget.status).toBe(200);

    const state = (await (await SELF.fetch("https://pq/api/state", { headers: auth })).json()) as { papers: unknown[]; history: unknown[] };
    expect(state.papers).toEqual([]);
    expect(state.history).toEqual([]);
  });
});
