import { readFile } from "node:fs/promises";
import path from "node:path";
import { NextRequest } from "next/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

async function proxy(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  const host = request.headers.get("host")?.split(":")[0];
  if (host !== "localhost" && host !== "127.0.0.1") {
    return Response.json({ detail: "Local operator access required" }, { status: 403 });
  }
  const origin = request.headers.get("origin");
  const expectedOrigin = `${request.nextUrl.protocol}//${request.headers.get("host")}`;
  if (origin && origin !== expectedOrigin) {
    return Response.json({ detail: "Cross-origin operator access denied" }, { status: 403 });
  }
  const site = request.headers.get("sec-fetch-site");
  if (site === "cross-site") return Response.json({ detail: "Cross-site access denied" }, { status: 403 });
  const segments = (await context.params).path;
  if (segments.some((segment) => !/^[a-zA-Z0-9_.-]+$/.test(segment))) {
    return Response.json({ detail: "Invalid path" }, { status: 400 });
  }
  const base = process.env.UTOPIA_API_URL || "http://127.0.0.1:8000";
  try {
    const token = process.env.UTOPIA_OPERATOR_TOKEN || (await readFile(
      path.join(process.env.UTOPIA_DATA || path.resolve(process.cwd(), "../.data"), "operator.token"),
      "utf8",
    )).trim();
    const headers: Record<string, string> = { Authorization: `Bearer ${token}` };
    if (request.headers.get("content-type")) headers["Content-Type"] = request.headers.get("content-type")!;
    if (request.headers.get("last-event-id")) headers["Last-Event-ID"] = request.headers.get("last-event-id")!;
    const backend = await fetch(`${base}/${segments.join("/")}${request.nextUrl.search}`, {
      method: request.method, headers, cache: "no-store", signal: request.signal,
      body: ["GET", "HEAD"].includes(request.method) ? undefined : await request.text(),
    });
    const responseHeaders = new Headers();
    for (const key of ["content-type", "content-disposition", "cache-control"]) {
      const value = backend.headers.get(key);
      if (value) responseHeaders.set(key, value);
    }
    responseHeaders.set("X-Content-Type-Options", "nosniff");
    responseHeaders.set("Cache-Control", "no-store");
    responseHeaders.set("X-Accel-Buffering", "no");
    return new Response(backend.body, { status: backend.status, headers: responseHeaders });
  } catch {
    return Response.json({ detail: "Operator server unavailable. Start the Python server first." }, { status: 503 });
  }
}

export const GET = proxy;
export const POST = proxy;
export const PUT = proxy;
