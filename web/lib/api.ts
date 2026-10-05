import type {
  CurrentPeriod,
  CustomerDetail,
  CustomerList,
  DayPoint,
  FeatureMargin,
  Meter,
  PriceSpec,
  Product,
  SimulationResult,
  Summary,
} from "./types";

/** Requests go through the Next.js route handler, which adds the API key server-side. */
const BASE = "/api/ue";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers: { "content-type": "application/json", ...init?.headers },
  });
  if (!res.ok) {
    const body = (await res.json().catch(() => ({}))) as { detail?: unknown };
    const detail = typeof body.detail === "string" ? body.detail : `request failed (${res.status})`;
    throw new ApiError(res.status, detail);
  }
  return (await res.json()) as T;
}

export const api = {
  summary: (days: number) => request<Summary>(`/v1/economics/summary?days=${days}`),
  customers: (days: number, limit = 500) => request<CustomerList>(`/v1/economics/customers?days=${days}&limit=${limit}`),
  features: (days: number) => request<FeatureMargin[]>(`/v1/economics/features?days=${days}`),
  timeseries: (days: number) => request<DayPoint[]>(`/v1/economics/timeseries?days=${days}`),
  customer: (id: string, days: number) =>
    request<CustomerDetail>(`/v1/economics/customers/${encodeURIComponent(id)}?days=${days}`),
  currentPeriod: (id: string) => request<CurrentPeriod>(`/v1/customers/${encodeURIComponent(id)}/current-period`),
  products: () => request<Product[]>("/v1/products"),
  meters: () => request<Meter[]>("/v1/meters"),
  simulate: (body: { product_id: string; days: number; prices: PriceSpec[] }, signal?: AbortSignal) =>
    request<SimulationResult>("/v1/simulations", { method: "POST", body: JSON.stringify(body), signal: signal ?? null }),
};
