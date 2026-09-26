import Link from "next/link";
import { SiteHeader } from "@/components/site-header";
import { Console } from "@/components/console";
import { CapabilityExplorer } from "@/components/capability-explorer";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";

const PIPELINE = [
  { label: "Model input", detail: "messy financial language, verbatim" },
  { label: "Candidate Semantic IR", detail: "18-field structured interpretation" },
  { label: "Schema verification", detail: "strict, fail-closed field contract" },
  { label: "Grounding", detail: "every claim tied to input evidence" },
  { label: "Authority lookup", detail: "capability registry routes and constrains" },
  { label: "Deterministic execution", detail: "accounting kernel / formula authority" },
];

const STATUS_WORDS = [
  { word: "VERIFIED", note: "deterministic execution passed", tone: "text-status-verified border-status-verified/35 bg-status-verified/10" },
  { word: "REVIEW_REQUIRED", note: "evidence insufficient — flagged, not guessed", tone: "text-status-review border-status-review/35 bg-status-review/10" },
  { word: "UNSUPPORTED", note: "no authority accepts it — refuses openly", tone: "text-status-unsupported border-status-unsupported/35 bg-status-unsupported/10" },
] as const;

export default function Home() {
  return (
    <div className="flex min-h-screen flex-col">
      <SiteHeader />

      <div className="relative">
        <div className="platrixa-grid pointer-events-none absolute inset-0 h-[420px]" aria-hidden />

        <main className="mx-auto w-full max-w-6xl flex-1 px-4 sm:px-6">
          {/* ---------- Hero ---------- */}
          <section className="pt-16 pb-12 sm:pt-24">
            <Badge
              variant="outline"
              className="mb-5 rounded-full border-accent/40 bg-accent-soft px-3 py-1 font-mono text-[11px] text-accent"
            >
              deterministic financial semantic validation
            </Badge>
            <h1 className="max-w-3xl text-4xl font-semibold leading-[1.1] tracking-tight sm:text-5xl">
              AI interprets.
              <br />
              <span className="text-accent">Deterministic authorities decide.</span>
            </h1>
            <p className="mt-5 max-w-2xl text-base leading-relaxed text-muted-foreground">
              Platrixa validates, grounds, and routes what a model understood about financial
              language — then lets deterministic authorities calculate the truth. Every result
              carries its evidence, and anything the runtime cannot prove becomes
              {" "}<span className="font-mono text-status-review">REVIEW_REQUIRED</span> instead of a confident guess.
            </p>

            <div className="mt-7 flex flex-wrap items-center gap-3">
              <Button asChild>
                <Link href="#console">Try the console</Link>
              </Button>
              <Button asChild variant="outline">
                <Link href="#capabilities">Browse the capability registry</Link>
              </Button>
            </div>

            <div className="mt-10 grid gap-3 sm:grid-cols-3">
              {STATUS_WORDS.map((s) => (
                <div
                  key={s.word}
                  className={`rounded-xl border px-4 py-3.5 ${s.tone}`}
                >
                  <p className="font-mono text-sm font-semibold tracking-tight">{s.word}</p>
                  <p className="mt-1 text-xs opacity-80">{s.note}</p>
                </div>
              ))}
            </div>
          </section>

          {/* ---------- Pipeline ---------- */}
          <section id="api" className="scroll-mt-20 pb-12">
            <div className="mb-5 flex items-end justify-between gap-4">
              <div>
                <h2 className="text-lg font-semibold tracking-tight">One pipeline, one authority</h2>
                <p className="mt-0.5 text-sm text-muted-foreground">
                  The UI renders results — it never decides them. This is the backend&apos;s documented flow.
                </p>
              </div>
              <code className="hidden font-mono text-xs text-muted-foreground md:block">
                api/main.py → Kernel.process
              </code>
            </div>
            <Card>
              <CardContent className="grid gap-px overflow-hidden bg-border/60 p-0 sm:grid-cols-6">
                {PIPELINE.map((stage, i) => (
                  <div key={stage.label} className="bg-card p-4">
                    <p className="font-mono text-[11px] text-accent">{String(i + 1).padStart(2, "0")}</p>
                    <p className="mt-1 text-sm font-medium leading-snug">{stage.label}</p>
                    <p className="mt-1 text-xs leading-relaxed text-muted-foreground">{stage.detail}</p>
                  </div>
                ))}
              </CardContent>
            </Card>
          </section>

          {/* ---------- Console ---------- */}
          <section className="pb-12">
            <div className="mb-5">
              <h2 className="text-lg font-semibold tracking-tight">Validation console</h2>
              <p className="mt-0.5 text-sm text-muted-foreground">
                Submit financial input and see exactly why the runtime reached its verdict.
              </p>
            </div>
            <Console />
          </section>

          {/* ---------- Capabilities ---------- */}
          <section className="pb-12">
            <CapabilityExplorer />
          </section>

          {/* ---------- API hint ---------- */}
          <section className="pb-16">
            <Card className="border-border/70 bg-card/60">
              <CardContent className="flex flex-wrap items-center justify-between gap-4 pt-6">
                <div>
                  <p className="text-sm font-medium">Bring your own integration</p>
                  <p className="mt-0.5 text-sm text-muted-foreground">
                    The same deterministic runtime behind this UI is exposed as a versioned developer API.
                  </p>
                </div>
                <code className="rounded-md border border-border bg-muted/50 px-3 py-2 font-mono text-xs">
                  POST /v1/process · GET /v1/capabilities
                </code>
              </CardContent>
            </Card>
          </section>
        </main>
      </div>

      <footer className="border-t border-border/70 py-6">
        <div className="mx-auto flex w-full max-w-6xl flex-wrap items-center justify-between gap-2 px-4 text-xs text-muted-foreground sm:px-6">
          <span>Platrixa — financial semantic validation infrastructure</span>
          <span className="font-mono">
            UI: Next.js · Tailwind · Vengeance UI · Skiper UI (free components)
          </span>
        </div>
      </footer>
    </div>
  );
}
