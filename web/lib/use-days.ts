"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useCallback } from "react";

/** The selected window lives in the URL, so a reload or a shared link shows the same view. */
export function useDays(fallback = 30): [number, (days: number) => void] {
  const params = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const parsed = Number(params.get("days"));
  const days = Number.isInteger(parsed) && parsed >= 1 && parsed <= 90 ? parsed : fallback;
  const setDays = useCallback(
    (next: number) => {
      const q = new URLSearchParams(params.toString());
      q.set("days", String(next));
      router.replace(`${pathname}?${q.toString()}`, { scroll: false });
    },
    [params, pathname, router],
  );
  return [days, setDays];
}
