"use client";

import Link from "next/link";
import { motion, useReducedMotion } from "framer-motion";
import { ShieldCheck } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { useEffect, useState } from "react";
import { fetchCapabilities } from "@/lib/api";
import type { CapabilitiesResponse } from "@/lib/types";

const PIPELINE = [
  { label: "Input", detail: "messy financial language, verbatim" },
  { label: "AI interpretation", detail: "candidate semantic IR" },
  { label: "Semantic validation", detail: "strict schema, fail-closed" },
  { label: "Evidence + grounding", detail: "every claim tied to input" },
  { label: "Authority", detail: "capability-registered, deterministic" },
  { label: "Decision", detail: "status + evidence, never a guess" },
] as const;

const STATUS_WORDS = [
  {
    word: "VERIFIED",
    note: "deterministic execution passed",
    tone: "text-status-verified border-status-verified/35 bg-status-verified/10",
  },
  {
    word: "REVIEW_REQUIRED",
    note: "evidence insufficient — flagged, not guessed",
    tone: "text-status-review border-status-review/35 bg-status-review/10",
  },
  {
    word: "UNSUPPORTED",
    note: "no authority accepts it — refuses openly",
    tone: "text-status-unsupported border-status-unsupported/35 bg-status-unsupported/10",
  },
] as const;

function PipelineFlow() {
  const reduceMotion = useReducedMotion();
  return (
    <ol className="grid gap-px overflow-hidden rounded-xl border border-border/70 bg-border/60 sm:grid-cols-6">
      {PIPELINE.map((stage, i) => (
        <motion.li
          key={stage.label}
          initial={reduceMotion ? false : { opacity: 0, y: 8 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true, margin: "-40px" }}
          transition={{ delay: reduceMotion ? 0 : i * 0.08, duration: 0.35 }}
          className="bg-card p-4"
        >
          <p className="font-mono text-[11px] text-accent">{String(i + 1).padStart(2, "0")}</p>
          <p className="mt-1 text-sm font-medium leading-snug">{stage.label}</p>
          <p className="mt-1 text-xs leading-relaxed text-muted-foreground">{stage.detail}</p>
        </motion.li>
      ))}
    </ol>
  );
}

/** Live registry metrics — computed from GET /v1/capabilities, nothing invented. */
function RegistryMetrics() {
  const [data, setData] = useState<CapabilitiesResponse | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    fetchCapabilities(controller.signal)
      .then(setData)
      .catch(() => setData(null));
    return () => controller.abort();
  }, []);

  if (!data) return null;
  const byAuthority = new Map<string, { total: number; supported: number }>();
  for (const cap of data.capabilities) {
    const entry = byAuthority.get(cap.authority) ?? { total: 0, supported: 0 };
    entry.total += 1;
    if (cap.supported_status === "SUPPORTED") entry.supported += 1;
    byAuthority.set(cap.authority, entry);
  }
  const supported = data.capabilities.filter((c) => c.supported_status === "SUPPORTED").length;
  const partial = data.capabilities.filter((c) => c.supported_status === "PARTIAL").length;
  const unsupported = data.capabilities.filter((c) => c.supported_status === "UNSUPPORTED").length;
  const planned = data.count - supported - partial - unsupported;

  return (
    <div className="mt-10">
      <p className="mb-3 font-mono text-[11px] uppercase tracking-[0.14em] text-muted-foreground">
        live registry · GET /v1/capabilities
      </p>
      <div className="grid gap-2 sm:grid-cols-3 lg:grid-cols-6">
        {[
          ["capabilities", data.count],
          ["supported", supported],
          ["partial", partial],
          ["documented refusals", unsupported],
          ["planned", planned],
          ["authorities", byAuthority.size],
        ].map(([label, value]) => (
          <div key={String(label)} className="rounded-lg border border-border/70 bg-card px-3.5 py-3">
            <p className="font-mono text-xl font-semibold tracking-tight">{value}</p>
            <p className="mt-0.5 text-[11px] leading-snug text-muted-foreground">{label}</p>
          </div>
        ))}
      </div>
    </div>
  );
}

export default function LandingPage() {
  return (
    <main className="flex-1">
      <div className="relative">
        <div className="platrixa-grid pointer-events-none absolute inset-x-0 top-0 h-[440px]" aria-hidden />

        <div className="mx-auto w-full max-w-6xl px-4 sm:px-6">
          {/* ---------- Hero ---------- */}
          <section className="pb-14 pt-16 sm:pt-24">
            <motion.p
              initial={false}
              animate={{ opacity: 1 }}
              className="mb-5 inline-block rounded-full border border-accent/40 bg-accent-soft px-3 py-1 font-mono text-[11px] text-accent"
            >
              financial semantic validation infrastructure
            </motion.p>
            <h1 className="max-w-3xl text-4xl font-semibold leading-[1.08] tracking-tight sm:text-5xl">
              AI understands.
              <br />
              <span className="text-accent">Platrixa validates.</span>
              <br />
              Deterministic authorities calculate and execute.
            </h1>
            <p className="mt-5 max-w-2xl text-base leading-relaxed text-muted-foreground">
              Turn uncertain AI-generated financial interpretations into evidence-backed,
              authority-compatible decisions. Every result carries the reason it was reached —
              and anything the runtime cannot prove becomes{" "}
              <span className="font-mono text-status-review">REVIEW_REQUIRED</span> instead of a
              confident guess.
            </p>

            <div className="mt-7 flex flex-wrap items-center gap-3">
              <Button asChild size="lg">
                <Link href="/validate">Validate input</Link>
              </Button>
              <Button asChild size="lg" variant="outline">
                <Link href="/capabilities">Explore capabilities</Link>
              </Button>
              <Link
                href="/developer"
                className="ml-1 text-sm font-medium text-muted-foreground underline-offset-4 hover:text-foreground hover:underline"
              >
                Developer API →
              </Link>
            </div>

            <RegistryMetrics />
          </section>

          {/* ---------- Status words ---------- */}
          <section className="pb-14">
            <div className="grid gap-3 sm:grid-cols-3">
              {STATUS_WORDS.map((s) => (
                <div key={s.word} className={`rounded-xl border px-4 py-3.5 ${s.tone}`}>
                  <p className="font-mono text-sm font-semibold tracking-tight">{s.word}</p>
                  <p className="mt-1 text-xs opacity-80">{s.note}</p>
                </div>
              ))}
            </div>
          </section>

          {/* ---------- Pipeline ---------- */}
          <section className="pb-14">
            <div className="mb-5">
              <h2 className="text-lg font-semibold tracking-tight">One pipeline, one authority</h2>
              <p className="mt-0.5 max-w-2xl text-sm text-muted-foreground">
                This is the runtime&apos;s actual architecture — the UI renders its outcomes and adds
                nothing to them.
              </p>
            </div>
            <PipelineFlow />
          </section>

          {/* ---------- Authorities ---------- */}
          <section className="pb-14">
            <div className="mb-5">
              <h2 className="text-lg font-semibold tracking-tight">Three deterministic authorities</h2>
              <p className="mt-0.5 max-w-2xl text-sm text-muted-foreground">
                Every journal entry and formula comes from a registry-registered authority. The
                counts below are the live registry, not marketing.
              </p>
            </div>
            <div className="grid gap-3 md:grid-cols-3">
              {[
                {
                  title: "Accounting Kernel",
                  body: "The deterministic journal-entry engine for FYJC book-keeping transactions — executes only what validated semantics and grounding support.",
                },
                {
                  title: "Formula Authority",
                  body: "Deterministic financial ratios and formulas — each a registered capability with an implementation reference and a proving test.",
                },
                {
                  title: "Finance Knowledge",
                  body: "Versioned finance-knowledge records with explicit sources — terminology served from records, not generated.",
                },
              ].map((a) => (
                <Card key={a.title} className="border-border/70 transition-colors hover:border-accent/30">
                  <CardContent className="pt-5">
                    <h3 className="text-sm font-semibold">{a.title}</h3>
                    <p className="mt-1.5 text-sm leading-relaxed text-muted-foreground">{a.body}</p>
                  </CardContent>
                </Card>
              ))}
            </div>
          </section>

          {/* ---------- Workflows + trust ---------- */}
          <section className="pb-16">
            <div className="grid gap-3 md:grid-cols-3">
              {[
                {
                  href: "/validate",
                  title: "Validation workspace",
                  body: "Text and document ingestion (PDF/image), grouped interpretation, validation journey, evidence, and the reason behind the decision.",
                  badge: "LIVE",
                },
                {
                  href: "/validate",
                  title: "Bulk validation",
                  body: "CSV / JSON batches with a review queue — designed, awaiting a backend batch endpoint.",
                  badge: "COMING SOON",
                },
                {
                  href: "/developer",
                  title: "Developer API",
                  body: "The versioned /v1 contract: request/response shapes, the six-state public status, and structured errors.",
                  badge: "LIVE",
                },
              ].map((card) => (
                <Card key={card.title} className="border-border/70 transition-colors hover:border-accent/30">
                  <CardContent className="pt-5">
                    <div className="flex items-center justify-between gap-2">
                      <h3 className="text-sm font-semibold">{card.title}</h3>
                      <span className="font-mono text-[10px] font-semibold tracking-wide text-muted-foreground">{card.badge}</span>
                    </div>
                    <p className="mt-1.5 text-sm leading-relaxed text-muted-foreground">{card.body}</p>
                    <Link href={card.href} className="mt-3 inline-block text-sm font-medium text-accent hover:underline">
                      Open →
                    </Link>
                  </CardContent>
                </Card>
              ))}
            </div>

            <div className="mt-3 flex flex-wrap items-center gap-2 rounded-xl border border-border/70 bg-card px-4 py-3.5">
              <ShieldCheck className="size-4 shrink-0 text-status-verified" aria-hidden />
              <p className="text-sm text-muted-foreground">
                Platrixa is a validation assistant — not financial, tax, legal, or investment advice,
                and{" "}
                <Link href="/trust" className="text-accent hover:underline">
                  VERIFIED is not a guarantee
                </Link>
                .
              </p>
            </div>
          </section>
        </div>
      </div>
    </main>
  );
}
