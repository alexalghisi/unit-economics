"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn } from "@/lib/utils";

const LINKS = [
  { href: "/", label: "Overview" },
  { href: "/simulator", label: "Pricing simulator" },
];

export function Nav() {
  const pathname = usePathname();
  return (
    <header className="border-b border-border bg-surface">
      <div className="mx-auto flex h-14 max-w-7xl items-center gap-6 px-4 sm:px-6">
        <Link href="/" className="flex items-center gap-2 text-sm font-semibold">
          <svg width="18" height="18" viewBox="0 0 18 18" aria-hidden="true">
            <rect x="1" y="9" width="4" height="8" rx="1" fill="var(--series-1)" />
            <rect x="7" y="5" width="4" height="12" rx="1" fill="var(--series-1)" opacity="0.7" />
            <rect x="13" y="1" width="4" height="16" rx="1" fill="var(--series-2)" />
          </svg>
          unit economics
        </Link>
        <nav className="flex gap-1">
          {LINKS.map((l) => {
            const active = l.href === "/" ? pathname === "/" || pathname.startsWith("/customers") : pathname.startsWith(l.href);
            return (
              <Link
                key={l.href}
                href={l.href}
                className={cn(
                  "rounded-md px-2.5 py-1.5 text-sm text-ink-2 hover:bg-surface-2 hover:text-ink",
                  active && "bg-surface-2 text-ink",
                )}
              >
                {l.label}
              </Link>
            );
          })}
        </nav>
      </div>
    </header>
  );
}
