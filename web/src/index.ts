// HTTP entry point: bearer-token auth, JSON API, static assets for everything else.
import { normalize } from "./links";
import type { PaperStack } from "./stack";

export { PaperStack } from "./stack";

const encoder = new TextEncoder();

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store" },
  });
}

function authorized(request: Request, env: Env): boolean {
  const header = request.headers.get("Authorization") ?? "";
  const token = header.startsWith("Bearer ") ? header.slice(7).trim() : "";
  if (!env.TOKEN || !token) return false;
  const a = encoder.encode(token);
  const b = encoder.encode(env.TOKEN);
  if (a.byteLength !== b.byteLength) return false;
  return crypto.subtle.timingSafeEqual(a, b);
}

async function readJson(request: Request): Promise<Record<string, unknown>> {
  try {
    const body = await request.json();
    return body && typeof body === "object" ? (body as Record<string, unknown>) : {};
  } catch {
    return {};
  }
}

function requireUrl(value: unknown): string {
  if (typeof value !== "string" || !value.trim()) throw new BadRequest("missing url");
  return value.trim();
}

function requireReview(body: Record<string, unknown>): { comment: string; usefulness: number } {
  const comment = typeof body.comment === "string" ? body.comment.trim() : "";
  if (!comment) throw new BadRequest("missing review comment");
  const usefulness = body.usefulness;
  if (typeof usefulness !== "number" || !Number.isFinite(usefulness) || usefulness < 0 || usefulness > 5) {
    throw new BadRequest("usefulness must be between 0 and 5");
  }
  return { comment, usefulness };
}

class BadRequest extends Error {}

async function api(request: Request, env: Env, stub: DurableObjectStub<PaperStack>): Promise<Response> {
  const url = new URL(request.url);
  const path = url.pathname;
  const method = request.method;
  const doResolve = String(env.RESOLVE ?? "1") !== "0";

  if (method === "GET" && path === "/api/state") return json(await stub.getState());

  if (method === "POST" && path === "/api/papers") {
    const body = await readJson(request);
    let normalized: { url: string; arxivId: string | null };
    try {
      normalized = normalize(requireUrl(body.url));
    } catch (err) {
      throw new BadRequest((err as Error).message);
    }
    const r = await stub.push(normalized.url, normalized.arxivId, doResolve);
    return json({ paper: r.paper, state: r.state }, r.ok ? 201 : 409);
  }

  if (method === "POST" && path === "/api/pop") {
    const body = await readJson(request);
    const r = await stub.pop(requireReview(body), typeof body.url === "string" ? body.url : undefined);
    return json(r, r.paper ? 200 : 404);
  }
  if (method === "POST" && path === "/api/take") {
    const body = await readJson(request);
    const r = await stub.takeBottom(requireReview(body), typeof body.url === "string" ? body.url : undefined);
    return json(r, r.paper ? 200 : 404);
  }
  if (method === "PATCH" && path === "/api/papers") {
    const body = await readJson(request);
    if (typeof body.conference !== "string") throw new BadRequest("missing conference");
    const r = await stub.setConference(requireUrl(body.url), body.conference);
    return json(r, r.paper ? 200 : 404);
  }
  if (method === "DELETE" && path === "/api/papers") {
    const r = await stub.remove(requireUrl(url.searchParams.get("url")));
    return json(r, r.paper ? 200 : 404);
  }
  if (method === "POST" && path === "/api/history/restore") {
    const body = await readJson(request);
    const r = await stub.restore(requireUrl(body.url));
    return json(r, r.paper ? 200 : 404);
  }
  if (method === "DELETE" && path === "/api/history") {
    const r = await stub.forget(requireUrl(url.searchParams.get("url")));
    return json(r, r.paper ? 200 : 404);
  }
  if (method === "POST" && path === "/api/import") {
    const body = await readJson(request);
    const r = await stub.importState(
      {
        papers: Array.isArray(body.papers) ? (body.papers as never[]) : [],
        history: Array.isArray(body.history) ? (body.history as never[]) : [],
      },
      doResolve,
    );
    return json(r);
  }
  return json({ error: "not found" }, 404);
}

export default {
  async fetch(request, env): Promise<Response> {
    const { pathname } = new URL(request.url);
    if (!pathname.startsWith("/api/")) return env.ASSETS.fetch(request);
    if (!authorized(request, env)) {
      return json({ error: "unauthorized" }, 401);
    }
    try {
      return await api(request, env, env.STACK.getByName("main"));
    } catch (err) {
      if (err instanceof BadRequest) return json({ error: err.message }, 400);
      console.error(err);
      return json({ error: "internal error" }, 500);
    }
  },
} satisfies ExportedHandler<Env>;
