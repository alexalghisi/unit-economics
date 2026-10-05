import { describe, expect, it } from "vitest";
import { UnitEconomics, retryDelayMs, type WireEvent } from "../src/index.js";

type Fault = number | "network";

function fakeServer(faults: Fault[] = []) {
  const stored = new Map<string, WireEvent>();
  const requests: WireEvent[][] = [];
  const fetchImpl = async (_url: string | URL | Request, init?: RequestInit): Promise<Response> => {
    const events = (JSON.parse(String(init?.body)) as { events: WireEvent[] }).events;
    requests.push(events);
    const fault = faults.shift();
    if (fault === "network") {
      events.forEach((e) => stored.set(e.idempotency_key, e)); // stored, but the response is lost
      throw new TypeError("network error");
    }
    if (fault !== undefined) return new Response("no", { status: fault, headers: { "retry-after": "2" } });
    const results = events.map((e, index) => {
      const status = stored.has(e.idempotency_key) ? "duplicate" : "accepted";
      stored.set(e.idempotency_key, e);
      return { index, status };
    });
    const count = (s: string) => results.filter((r) => r.status === s).length;
    return Response.json({ accepted: count("accepted"), duplicates: count("duplicate"), invalid: 0, results });
  };
  return { stored, requests, fetch: fetchImpl as typeof fetch };
}

const llm = { customerId: "acme", feature: "chat", model: "m", inputTokens: 10, outputTokens: 2 };

describe("UnitEconomics", () => {
  it("batches by size and flushes the rest", async () => {
    const server = fakeServer();
    const ue = new UnitEconomics({ apiKey: "k", fetch: server.fetch, batchSize: 10 });
    for (let i = 0; i < 25; i++) ue.trackLlm(llm);
    await ue.flush();
    expect(server.requests.map((r) => r.length)).toEqual([10, 10, 5]);
    expect(ue.stats.accepted).toBe(25);
  });

  it("retries with the same idempotency keys after a lost response", async () => {
    const server = fakeServer(["network", 503]);
    const sleeps: number[] = [];
    const ue = new UnitEconomics({
      apiKey: "k",
      fetch: server.fetch,
      sleep: async (ms) => void sleeps.push(ms),
      random: () => 0.5,
    });
    ue.trackLlm(llm);
    ue.track({ name: "document.processed", customerId: "acme", metadata: { pages: 3 } });
    await ue.flush();
    expect(server.requests).toHaveLength(3);
    expect(server.requests[0]).toEqual(server.requests[2]);
    expect(server.stored.size).toBe(2);
    expect(ue.stats).toMatchObject({ accepted: 0, duplicates: 2, failed: 0 });
    expect(sleeps).toEqual([125, 2000]); // jittered backoff, then Retry-After
  });

  it("does not retry client errors", async () => {
    const server = fakeServer([401]);
    const errors: string[] = [];
    const ue = new UnitEconomics({ apiKey: "k", fetch: server.fetch, onError: (e) => errors.push(e) });
    ue.trackLlm(llm);
    await ue.flush();
    expect(server.requests).toHaveLength(1);
    expect(errors).toEqual(["HTTP 401"]);
    expect(ue.stats.failed).toBe(1);
  });

  it("flushes on the interval", async () => {
    const server = fakeServer();
    const ue = new UnitEconomics({ apiKey: "k", fetch: server.fetch, flushIntervalMs: 5 });
    ue.trackLlm(llm);
    await new Promise((r) => setTimeout(r, 30));
    expect(server.stored.size).toBe(1);
  });
});

describe("retryDelayMs", () => {
  it("caps exponential backoff", () => {
    expect(retryDelayMs(10, null, 100, 1000, () => 1)).toBe(1000);
    expect(retryDelayMs(1, "0", 100, 1000, () => 1)).toBe(0);
    expect(retryDelayMs(1, "soon", 100, 1000, () => 1)).toBe(200);
  });
});
