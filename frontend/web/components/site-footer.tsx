import Link from "next/link";

export function SiteFooter() {
  return (
    <footer className="border-t border-border/70 py-6">
      <div className="mx-auto flex w-full max-w-6xl flex-wrap items-center justify-between gap-2 px-4 text-xs text-muted-foreground sm:px-6">
        <span>Platrixa — financial semantic validation infrastructure</span>
        <span className="font-mono">
          Next.js · Tailwind · Vengeance UI · Skiper UI (free components)
        </span>
      </div>
      <div className="mx-auto mt-2 flex w-full max-w-6xl flex-wrap gap-4 px-4 text-[11px] text-muted-foreground/70 sm:px-6">
        <Link href="/console" className="hover:text-foreground">
          Console
        </Link>
        <Link href="/capabilities" className="hover:text-foreground">
          Capabilities
        </Link>
        <Link href="/developer" className="hover:text-foreground">
          Developer API
        </Link>
      </div>
    </footer>
  );
}
