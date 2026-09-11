import { env, SELF } from "cloudflare:test";
import { describe, expect, it } from "vitest";

const fresh = () => env.STACK.getByName(`test-${crypto.randomUUID()}`);
const urls = (state: { papers: { url: string }[] }) => state.papers.map((p) => p.url);
const hist = (state: { history: { url: string }[] }) => state.history.map((p) => p.url);

describe("PaperStack", () => {
  it("pop is LIFO, take is FIFO, both archive", async () => {
    const s = fresh();
    for (const u of ["a", "b", "c"]) expect((await s.push(u, null, false)).ok).toBe(true);
    expect((await s.push("a", null, false)).ok).toBe(false);
    expect((await s.pop()).paper?.url).toBe("c");
    expect((await s.takeBottom()).paper?.url).toBe("a");
    const r = await s.remove("b");
    expect(r.paper?.url).toBe("b");
    expect(urls(r.state)).toEqual([]);
    expect(hist(r.state)).toEqual(["c", "a", "b"]);
    expect(r.state.history.every((p) => p.read_at)).toBe(true);
    expect((await s.pop()).paper).toBeNull();
    expect((await s.takeBottom()).paper).toBeNull();
  });

  it("restore, forget, version bumps, persistence", async () => {
    const s = fresh();
    await s.push("a", null, false);
    const v1 = (await s.getState()).version;
    await s.pop();
    const restored = await s.restore("a");
    expect(restored.paper?.read_at).toBeNull();
    expect(urls(restored.state)).toEqual(["a"]);
    expect(hist(restored.state)).toEqual([]);
    expect(restored.state.version).toBeGreaterThan(v1);
    expect((await s.restore("missing")).paper).toBeNull();
    await s.pop();
    expect((await s.forget("a")).paper?.url).toBe("a");
    expect(hist(await s.getState())).toEqual([]);
  });

  it("applyResolved updates papers and history", async () => {
    const s = fresh();
    await s.push("https://x.org/p", null, false);
    expect(await s.applyResolved("https://x.org/p", { title: "T", pdf_url: "https://x.org/p.pdf", arxiv_id: null })).toBe(true);
    const p = (await s.getState()).papers[0];
    expect(p.title).toBe("T");
    expect(p.pdf_url).toBe("https://x.org/p.pdf");
    expect(await s.applyResolved("nope", { title: "T", pdf_url: null, arxiv_id: null })).toBe(false);
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

    const pop = await SELF.fetch("https://pq/api/pop", { method: "POST", headers: auth });
    expect(pop.status).toBe(200);
    expect((await SELF.fetch("https://pq/api/pop", { method: "POST", headers: auth })).status).toBe(404);

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
