"use client";

import { useEffect, useState } from "react";
import { Link001, Link003 } from "@/components/ui/skiper-ui/skiper40";
import { fetchHealth } from "@/lib/api";
import { cn } from "@/lib/utils";

/** Header — brand, Skiper UI animated links, live API connection pill. */
export function SiteHeader() {
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
        <div className="flex items-center gap-3">
          <span
            className="grid size-8 place-items-center rounded-md border border-accent/40 bg-accent-soft font-mono text-xs font-bold tracking-tight text-accent"
            aria-hidden
          >
            PLX
          </span>
          <div className="leading-tight">
            <p className="text-sm font-semibold tracking-tight">Platrixa</p>
            <p className="text-[11px] text-muted-foreground">
              Financial semantic validation
            </p>
          </div>
        </div>

        <nav className="hidden items-center gap-6 text-sm text-muted-foreground md:flex" aria-label="Primary">
          <Link001 href="#console" className="hover:text-foreground">
            Console
          </Link001>
          <Link003 href="#capabilities" className="hover:text-foreground">
            Capabilities
          </Link003>
          <Link001 href="#api" className="hover:text-foreground">
            API
          </Link001>
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
          {apiUp === null ? "checking API…" : apiUp ? "API connected" : "API offline — demo data"}
        </div>
      </div>
    </header>
  );
}
