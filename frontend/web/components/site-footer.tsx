import Link from "next/link";

/**
 * Product footer — only real links and real details. Sections without a
 * real destination (e.g. no published privacy policy) render as disabled
 * entries rather than dead or invented URLs.
 */

const GITHUB_URL = "https://github.com/pawarpranay2010-sketch/financial-timeline-engine-v0";
const BACKEND_HEALTH = "https://financial-timeline-engine-v0.onrender.com/api/v1/health";

const COLUMNS: Array<{
  title: string;
  links: Array<{ label: string; href?: string; external?: boolean; disabled?: boolean }>;
}> = [
  {
    title: "Product",
    links: [
      { label: "Validate", href: "/validate" },
      { label: "Capabilities", href: "/capabilities" },
      { label: "Developer", href: "/developer" },
    ],
  },
  {
    title: "Resources",
    links: [
      { label: "Documentation", href: "/developer" },
      { label: "API reference", href: "/developer" },
      { label: "GitHub", href: GITHUB_URL, external: true },
      { label: "Status", href: BACKEND_HEALTH, external: true },
    ],
  },
  {
    title: "Trust",
    links: [
      { label: "Trust & Safety", href: "/trust" },
      { label: "Security notes", href: "/trust" },
      { label: "Privacy", disabled: true },
      { label: "Terms", disabled: true },
    ],
  },
  {
    title: "Company",
    links: [
      { label: "About", href: "https://github.com/pawarpranay2010-sketch", external: true },
      { label: "Contact", disabled: true },
      { label: "Support", disabled: true },
    ],
  },
];

export function SiteFooter() {
  return (
    <footer className="border-t border-border/70 py-8">
      <div className="mx-auto w-full max-w-6xl px-4 sm:px-6">
        <div className="grid gap-8 sm:grid-cols-2 lg:grid-cols-4">
          {COLUMNS.map((col) => (
            <div key={col.title}>
              <p className="font-mono text-[11px] uppercase tracking-[0.14em] text-muted-foreground">{col.title}</p>
              <ul className="mt-3 space-y-2">
                {col.links.map((link) => (
                  <li key={link.label}>
                    {link.disabled ? (
                      <span className="cursor-not-allowed text-sm text-muted-foreground/50" title="Not yet available">
                        {link.label}
                      </span>
                    ) : link.external ? (
                      <a
                        href={link.href}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-sm text-muted-foreground transition-colors hover:text-foreground"
                      >
                        {link.label} ↗
                      </a>
                    ) : (
                      <Link href={link.href ?? "/"} className="text-sm text-muted-foreground transition-colors hover:text-foreground">
                        {link.label}
                      </Link>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
        <div className="mt-8 flex flex-col gap-1.5 border-t border-border/60 pt-5 sm:flex-row sm:items-center sm:justify-between">
          <p className="text-xs text-muted-foreground">
            © 2026 Platrixa · Built by Pranay Pawar
          </p>
          <p className="font-mono text-[11px] text-muted-foreground/70">
            Next.js · Tailwind · Vengeance UI · Skiper UI (free components)
          </p>
        </div>
      </div>
    </footer>
  );
}
