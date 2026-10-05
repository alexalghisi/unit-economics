export type Metadata = Record<string, string | number | boolean | null>;

export interface LlmUsage {
  customerId: string;
  feature: string;
  model: string;
  inputTokens: number;
  outputTokens: number;
  /** Billed at the cached rate; not included in `inputTokens`. */
  cachedTokens?: number;
  metadata?: Metadata;
  timestamp?: Date;
  idempotencyKey?: string;
}

export interface MeterEvent {
  name: string;
  customerId: string;
  feature?: string;
  metadata?: Metadata;
  timestamp?: Date;
  idempotencyKey?: string;
}

export interface Options {
  apiKey: string;
  baseUrl?: string;
  batchSize?: number;
  flushIntervalMs?: number;
  maxRetries?: number;
  backoffBaseMs?: number;
  backoffMaxMs?: number;
  fetch?: typeof fetch;
  sleep?: (ms: number) => Promise<void>;
  random?: () => number;
  onError?: (error: string, events: WireEvent[]) => void;
}

export interface WireEvent {
  kind: "llm_usage" | "event";
  idempotency_key: string;
  customer_id: string;
  timestamp: string;
  feature: string;
  metadata: Metadata;
  name?: string;
  model?: string;
  input_tokens?: number;
  output_tokens?: number;
  cached_tokens?: number;
}

export interface Stats {
  accepted: number;
  duplicates: number;
  invalid: number;
  failed: number;
}

const RETRYABLE = new Set([408, 409, 425, 429, 500, 502, 503, 504]);

/** Retry delay: Retry-After if the server sent one, otherwise exponential backoff with full jitter. */
export function retryDelayMs(attempt: number, retryAfter: string | null, base: number, max: number, random: () => number): number {
  const seconds = retryAfter === null ? Number.NaN : Number(retryAfter);
  if (Number.isFinite(seconds) && seconds >= 0) return seconds * 1000;
  return random() * Math.min(max, base * 2 ** attempt);
}

export class UnitEconomics {
  private readonly opts: Required<Omit<Options, "onError">> & Pick<Options, "onError">;
  private queue: WireEvent[] = [];
  private timer: ReturnType<typeof setTimeout> | undefined;
  private inflight: Promise<void> = Promise.resolve();
  readonly stats: Stats = { accepted: 0, duplicates: 0, invalid: 0, failed: 0 };

  constructor(options: Options) {
    this.opts = {
      baseUrl: "http://localhost:8000",
      batchSize: 100,
      flushIntervalMs: 1000,
      maxRetries: 5,
      backoffBaseMs: 250,
      backoffMaxMs: 20_000,
      fetch: globalThis.fetch.bind(globalThis),
      sleep: (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
      random: Math.random,
      ...options,
    };
  }

  trackLlm(usage: LlmUsage): string {
    return this.enqueue({
      kind: "llm_usage",
      customer_id: usage.customerId,
      feature: usage.feature,
      model: usage.model,
      input_tokens: usage.inputTokens,
      output_tokens: usage.outputTokens,
      cached_tokens: usage.cachedTokens ?? 0,
      metadata: usage.metadata ?? {},
      ...this.envelope(usage.timestamp, usage.idempotencyKey),
    });
  }

  track(event: MeterEvent): string {
    return this.enqueue({
      kind: "event",
      name: event.name,
      customer_id: event.customerId,
      feature: event.feature ?? "",
      metadata: event.metadata ?? {},
      ...this.envelope(event.timestamp, event.idempotencyKey),
    });
  }

  /** Sends everything queued so far; resolves once it has been delivered or given up on. */
  flush(): Promise<void> {
    if (this.timer !== undefined) clearTimeout(this.timer);
    this.timer = undefined;
    while (this.queue.length > 0) {
      const batch = this.queue.splice(0, this.opts.batchSize);
      // Batches go out one after another so a retrying batch doesn't race the next one.
      this.inflight = this.inflight.then(() => this.send(batch));
    }
    return this.inflight;
  }

  private envelope(timestamp: Date | undefined, key: string | undefined): Pick<WireEvent, "idempotency_key" | "timestamp"> {
    // The key is fixed now, so retrying a batch the server may already have stored is safe.
    return { idempotency_key: key ?? crypto.randomUUID(), timestamp: (timestamp ?? new Date()).toISOString() };
  }

  private enqueue(event: WireEvent): string {
    this.queue.push(event);
    if (this.queue.length >= this.opts.batchSize) {
      void this.flush();
    } else if (this.timer === undefined) {
      this.timer = setTimeout(() => void this.flush(), this.opts.flushIntervalMs);
    }
    return event.idempotency_key;
  }

  private async send(batch: WireEvent[]): Promise<void> {
    for (let attempt = 0; ; attempt++) {
      let error: string;
      let retryAfter: string | null = null;
      try {
        const res = await this.opts.fetch(`${this.opts.baseUrl}/v1/events/ingest`, {
          method: "POST",
          headers: { "content-type": "application/json", authorization: `Bearer ${this.opts.apiKey}` },
          body: JSON.stringify({ events: batch }),
          keepalive: true,
        });
        if (res.ok) {
          this.record((await res.json()) as IngestResponse, batch);
          return;
        }
        error = `HTTP ${res.status}`;
        if (!RETRYABLE.has(res.status)) return this.fail(error, batch);
        retryAfter = res.headers.get("retry-after");
      } catch (e) {
        error = e instanceof Error ? e.message : String(e);
      }
      if (attempt >= this.opts.maxRetries) return this.fail(error, batch);
      await this.opts.sleep(retryDelayMs(attempt, retryAfter, this.opts.backoffBaseMs, this.opts.backoffMaxMs, this.opts.random));
    }
  }

  private record(body: IngestResponse, batch: WireEvent[]): void {
    this.stats.accepted += body.accepted;
    this.stats.duplicates += body.duplicates;
    this.stats.invalid += body.invalid;
    const invalid = body.results.filter((r) => r.status === "invalid");
    if (invalid.length > 0) {
      const events = invalid.map((r) => batch[r.index]).filter((e): e is WireEvent => e !== undefined);
      this.opts.onError?.(`${invalid.length} invalid events`, events);
    }
  }

  private fail(error: string, batch: WireEvent[]): void {
    this.stats.failed += batch.length;
    this.opts.onError?.(error, batch);
  }
}

interface IngestResponse {
  accepted: number;
  duplicates: number;
  invalid: number;
  results: { index: number; status: "accepted" | "duplicate" | "invalid" }[];
}
