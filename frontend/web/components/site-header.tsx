"use client";

import { useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { Menu, X } from "lucide-react";
import { cn } from "@/lib/utils";

const NAV_ITEMS = [
  { href: "/validate", label: "Validate" },
  { href: "/capabilities", label: "Capabilities" },
  { href: "/developer", label: "Developer" },
  { href: "/trust", label: "Trust & Safety" },
] as const;

function isActivePath(pathname: string, href: string) {
  if (href === "/validate") return pathname === "/validate" || pathname === "/console";
  return pathname === href || pathname.startsWith(`${href}/`);
}

export function SiteHeader() {
  const pathname = usePathname();
  const [open, setOpen] = useState(false);
  // Event-driven close: nav links close the sheet on click. No
  // setState-in-effect (no cascading renders); route changes with the
  // sheet still open are covered because every link closes it.
  const close = () => setOpen(false);

  return (
    <header className="sticky top-0 z-20 border-b border-border/70 bg-background/80 backdrop-blur-sm">
      <div className="mx-auto flex h-14 w-full max-w-6xl items-center justify-between gap-4 px-4 sm:px-6">
        <Link href="/" className="flex items-center gap-3" aria-label="Platrixa home" onClick={close}>
          <span className="grid size-8 place-items-center rounded-md border border-accent/40 bg-accent-soft font-mono text-xs font-bold tracking-tight text-accent" aria-hidden>
            PLX
          </span>
          <span className="leading-tight">
            <span className="block text-sm font-semibold tracking-tight">Platrixa</span>
            <span className="block text-[11px] text-muted-foreground">Financial semantic validation</span>
          </span>
        </Link>

        {/* Desktop nav */}
        <nav className="hidden items-center gap-6 text-sm text-muted-foreground md:flex" aria-label="Primary">
          {NAV_ITEMS.map((item) => (
            <Link
              key={item.href}
              href={item.href}
              className={cn(
                "transition-colors hover:text-foreground",
                isActivePath(pathname, item.href) && "text-foreground",
              )}
            >
              {item.label}
            </Link>
          ))}
        </nav>

        {/* Mobile hamburger */}
        <button
          type="button"
          className="inline-flex size-9 items-center justify-center rounded-md border border-border text-muted-foreground transition-colors hover:text-foreground md:hidden"
          aria-expanded={open}
          aria-controls="mobile-nav"
          aria-label={open ? "Close navigation" : "Open navigation"}
          onClick={() => setOpen((v) => !v)}
        >
          {open ? <X className="size-4" aria-hidden /> : <Menu className="size-4" aria-hidden />}
        </button>
      </div>

      {/* Mobile sheet */}
      {open && (
        <nav id="mobile-nav" className="border-t border-border/70 bg-background px-4 py-3 md:hidden" aria-label="Primary mobile">
          <ul className="space-y-1">
            {NAV_ITEMS.map((item) => (
              <li key={item.href}>
                <Link
                  href={item.href}
                  onClick={close}
                  className={cn(
                    "block rounded-lg px-3 py-2.5 text-sm transition-colors hover:bg-muted",
                    isActivePath(pathname, item.href) ? "bg-accent-soft font-medium text-accent" : "text-muted-foreground",
                  )}
                >
                  {item.label}
                </Link>
              </li>
            ))}
          </ul>
        </nav>
      )}
    </header>
  );
}
