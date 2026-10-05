import type { NextRequest } from "next/server";

/**
 * Forwards dashboard requests to the API with the organization key attached here, on the
 * server, so the key never reaches the browser.
 */
const API_URL = process.env.UE_API_URL ?? "http://127.0.0.1:8130";
const API_KEY = process.env.UE_API_KEY ?? "";

async function forward(request: NextRequest, path: string[]): Promise<Response> {
  const url = new URL(`${API_URL}/${path.map(encodeURIComponent).join("/")}`);
  url.search = request.nextUrl.search;
  const upstream = await fetch(url, {
    method: request.method,
    headers: { authorization: `Bearer ${API_KEY}`, "content-type": "application/json" },
    body: request.method === "GET" ? null : await request.text(),
    cache: "no-store",
  });
  return new Response(upstream.body, {
    status: upstream.status,
    headers: { "content-type": upstream.headers.get("content-type") ?? "application/json" },
  });
}

type Context = { params: Promise<{ path: string[] }> };

export async function GET(request: NextRequest, { params }: Context) {
  return forward(request, (await params).path);
}

export async function POST(request: NextRequest, { params }: Context) {
  return forward(request, (await params).path);
}
