"use client";

import Link from "next/link";
import { motion, useReducedMotion } from "framer-motion";
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

/** Quiet, real-data strip: live registry counts (no fabricated stats). */
function RegistryStrip() {
  const [data, setData] = useState<CapabilitiesResponse | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    fetchCapabilities(controller.signal)
      .then(setData)
      .catch(() => setData(null));
    return () => controller.abort();
  }, []);

  if (!data) return null;
  const kernel = data.registry_summary["ACCOUNTING_KERNEL"];
  const supported = data.capabilities.filter((c) => c.supported_status === "SUPPORTED").length;
  const unsupported = data.capabilities.filter((c) => c.supported_status === "UNSUPPORTED").length;
  return (
    <p className="mt-6 font-mono text-xs text-muted-foreground">
      live registry — {data.count} capabilities · {supported} supported · {unsupported} documented
      refusals
      {kernel ? ` · kernel: ${kernel.SUPPORTED ?? 0} supported / ${kernel.UNSUPPORTED ?? 0} refused` : ""}
    </p>
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
                <Link href="/console">Process financial input</Link>
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

            <div className="mt-10 grid gap-3 sm:grid-cols-3">
              {STATUS_WORDS.map((s) => (
                <div key={s.word} className={`rounded-xl border px-4 py-3.5 ${s.tone}`}>
                  <p className="font-mono text-sm font-semibold tracking-tight">{s.word}</p>
                  <p className="mt-1 text-xs opacity-80">{s.note}</p>
                </div>
              ))}
            </div>

            <RegistryStrip />
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

          {/* ---------- Routes ---------- */}
          <section className="pb-16">
            <div className="grid gap-3 md:grid-cols-3">
              {[
                {
                  href: "/console",
                  title: "Validation console",
                  body: "Submit a narration and see the interpretation, the validation journey, the evidence, and the decision — with the reason.",
                },
                {
                  href: "/capabilities",
                  title: "Capability explorer",
                  body: "The live registry: what is supported, what is partial, and what is refused — with the refusal evidence.",
                },
                {
                  href: "/developer",
                  title: "Developer API",
                  body: "The versioned /v1 contract: request/response shapes, the six-state public status, and structured errors.",
                },
              ].map((card) => (
                <Card
                  key={card.href}
                  className="border-border/70 transition-colors hover:border-accent/30"
                >
                  <CardContent className="pt-5">
                    <h3 className="text-sm font-semibold">{card.title}</h3>
                    <p className="mt-1.5 text-sm leading-relaxed text-muted-foreground">
                      {card.body}
                    </p>
                    <Link
                      href={card.href}
                      className="mt-3 inline-block text-sm font-medium text-accent hover:underline"
                    >
                      Open →
                    </Link>
                  </CardContent>
                </Card>
              ))}
            </div>
          </section>
        </div>
      </div>
    </main>
  );
}
