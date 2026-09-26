"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { Link001 } from "@/components/skiper-links";
import { fetchHealth } from "@/lib/api";
import { cn } from "@/lib/utils";

const NAV = [
  { href: "/console", label: "Console" },
  { href: "/capabilities", label: "Capabilities" },
  { href: "/developer", label: "Developer" },
] as const;

/** Header — brand, route nav (Skiper animated links), live API pill. */
export function SiteHeader() {
  const pathname = usePathname();
  const [apiUp, setApiUp] = useState<boolean | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    let cancelled = false;
    fetchHealth(controller.signal).then((ok) => {
      if (!cancelled) setApiUp(ok);
    });
    return () => {
      cancelled = true;
      controller.abort();
    };
  }, []);

  return (
    <header className="sticky top-0 z-20 border-b border-border/70 bg-background/80 backdrop-blur-sm">
      <div className="mx-auto flex h-14 w-full max-w-6xl items-center justify-between gap-4 px-4 sm:px-6">
        <Link href="/" className="flex items-center gap-3" aria-label="Platrixa home">
          <span
            className="grid size-8 place-items-center rounded-md border border-accent/40 bg-accent-soft font-mono text-xs font-bold tracking-tight text-accent"
            aria-hidden
          >
            PLX
          </span>
          <span className="leading-tight">
            <span className="block text-sm font-semibold tracking-tight">Platrixa</span>
            <span className="block text-[11px] text-muted-foreground">
              Financial semantic validation
            </span>
          </span>
        </Link>

        <nav
          className="hidden items-center gap-6 text-sm text-muted-foreground md:flex"
          aria-label="Primary"
        >
          {NAV.map((item) => {
            const active = pathname === item.href;
            return (
              <span key={item.href} className={cn(active && "text-foreground")}>
                <Link001 href={item.href} className={cn("hover:text-foreground", active && "text-accent")}>
                  {item.label}
                </Link001>
              </span>
            );
          })}
        </nav>

        <div
          className={cn(
            "flex items-center gap-2 rounded-full border px-3 py-1 text-xs",
            apiUp === null && "border-border text-muted-foreground",
            apiUp === true && "border-status-verified/35 bg-status-verified/10 text-status-verified",
            apiUp === false && "border-status-review/35 bg-status-review/10 text-status-review",
          )}
          role="status"
          aria-live="polite"
        >
          <span
            className={cn(
              "size-1.5 rounded-full",
              apiUp === null && "bg-muted-foreground",
              apiUp === true && "bg-status-verified",
              apiUp === false && "bg-status-review",
            )}
            aria-hidden
          />
          {apiUp === null ? "checking API…" : apiUp ? "LIVE API" : "API offline"}
        </div>
      </div>

      {/* Mobile nav row */}
      <nav
        className="flex items-center gap-5 border-t border-border/60 px-4 py-2 text-sm text-muted-foreground md:hidden"
        aria-label="Primary mobile"
      >
        {NAV.map((item) => (
          <Link
            key={item.href}
            href={item.href}
            className={cn(
              "transition-colors hover:text-foreground",
              pathname === item.href && "text-accent",
            )}
          >
            {item.label}
          </Link>
        ))}
      </nav>
    </header>
  );
}
