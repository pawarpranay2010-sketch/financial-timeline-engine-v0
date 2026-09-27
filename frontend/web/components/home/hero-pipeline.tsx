"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { motion, useReducedMotion } from "framer-motion";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/**
 * Hero — product statement plus an animated validation pipeline.
 *
 * The pipeline is a labeled product EXAMPLE (fixed input, fixed outcome):
 * a pulse travels Input → Semantic IR → Evidence → Grounding → Authority →
 * VERIFIED on a loop to communicate how data moves through the system.
 * It never impersonates a live backend call — the live workspace is the
 * section directly below.
 */

const DEMO_INPUT = "Purchased furniture for cash Rs. 15,000";

const STAGES = [
  { label: "INPUT", detail: DEMO_INPUT },
  { label: "SEMANTIC IR", detail: "18-field candidate, model-proposed" },
  { label: "EVIDENCE", detail: "every claim tied to the input" },
  { label: "GROUNDING", detail: "schema + evidence checks" },
  { label: "AUTHORITY", detail: "ACCOUNTING_KERNEL" },
  { label: "VERIFIED", detail: "deterministic execution passed" },
] as const;

const CYCLE_MS = 1900; // dwell per pipeline stage — quick, precise, not frenetic
const RESULT_INDEX = STAGES.length - 1;

function usePipelineIndex(reduceMotion: boolean | null): number {
  const [index, setIndex] = useState(0);
  useEffect(() => {
    if (reduceMotion) return; // static full-pipeline view, no loop
    const timer = window.setInterval(() => {
      setIndex((i) => (i + 1) % (STAGES.length + 1)); // +1 → brief full-lit pause
    }, CYCLE_MS);
    return () => window.clearInterval(timer);
  }, [reduceMotion]);
  return index;
}

function PipelineStage({
  stage,
  index,
  activeIndex,
  reduceMotion,
}: {
  stage: (typeof STAGES)[number];
  index: number;
  activeIndex: number;
  reduceMotion: boolean | null;
}) {
  const isResult = index === RESULT_INDEX;
  const lit = activeIndex >= index || activeIndex === STAGES.length;
  const active = activeIndex === index;

  return (
    <motion.li
      initial={reduceMotion ? false : { opacity: 0, y: 10 }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true, margin: "-60px" }}
      transition={{ delay: reduceMotion ? 0 : index * 0.07, duration: 0.3 }}
      className="min-w-0 flex-1"
      aria-current={active ? "step" : undefined}
    >
      <div
        className={cn(
          "relative rounded-lg border px-3 py-2.5 transition-colors duration-300",
          isResult
            ? lit
              ? "border-status-verified/50 bg-status-verified/10"
              : "border-status-verified/25 bg-status-verified/5"
            : active
              ? "border-accent/60 bg-accent-soft"
              : lit
                ? "border-accent/25 bg-card"
                : "border-border/70 bg-card",
        )}
      >
        {active && !reduceMotion && (
          <motion.span
            layoutId="platrixa-pulse"
            className="absolute inset-0 rounded-lg ring-1 ring-accent/50"
            transition={{ type: "spring", stiffness: 320, damping: 30 }}
            aria-hidden
          />
        )}
        <p
          className={cn(
            "font-mono text-[11px] font-semibold tracking-[0.12em]",
            isResult
              ? "text-status-verified"
              : active
                ? "text-accent"
                : lit
                  ? "text-foreground/90"
                  : "text-muted-foreground",
          )}
        >
          {isResult && "✓ "}
          {stage.label}
        </p>
        <p className="mt-1 truncate text-[11px] leading-snug text-muted-foreground" title={stage.detail}>
          {stage.detail}
        </p>
      </div>
      {index < RESULT_INDEX && (
        <div className="mt-1 flex justify-center" aria-hidden>
          <span
            className={cn(
              "h-4 w-px transition-colors duration-300",
              activeIndex > index ? "bg-accent/60" : "bg-border",
            )}
          />
        </div>
      )}
    </motion.li>
  );
}

export function HeroPipeline() {
  const reduceMotion = useReducedMotion();
  const activeIndex = usePipelineIndex(reduceMotion);

  return (
    <section className="relative pb-14 pt-12 sm:pt-16" aria-labelledby="hero-heading">
      <div className="platrixa-grid pointer-events-none absolute inset-x-0 -top-14 h-[440px]" aria-hidden />

      <div className="relative">
        <p className="mb-5 inline-block rounded-full border border-accent/40 bg-accent-soft px-3 py-1 font-mono text-[11px] text-accent">
          financial semantic validation infrastructure
        </p>
        <h1 id="hero-heading" className="max-w-3xl text-4xl font-semibold leading-[1.08] tracking-tight sm:text-5xl">
          AI understands.
          <br />
          <span className="text-accent">Platrixa validates.</span>
          <br />
          Deterministic authorities calculate and execute.
        </h1>
        <p className="mt-5 max-w-2xl text-base leading-relaxed text-muted-foreground">
          Platrixa sits between AI-generated financial understanding and deterministic financial
          execution. Every result carries the evidence and the reason it was reached — and anything
          the runtime cannot prove becomes{" "}
          <span className="font-mono text-status-review">REVIEW_REQUIRED</span> instead of a
          confident guess.
        </p>

        <div className="mt-7 flex flex-wrap items-center gap-3">
          <Button asChild size="lg">
            <Link href="#try-it">Try it live</Link>
          </Button>
          <Button asChild size="lg" variant="outline">
            <Link href="#validation-journey">See the validation journey</Link>
          </Button>
          <Link
            href="/developer"
            className="ml-1 text-sm font-medium text-muted-foreground underline-offset-4 hover:text-foreground hover:underline"
          >
            Developer API →
          </Link>
        </div>

        {/* Animated validation pipeline — labeled product example */}
        <div className="mt-10">
          <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
            <h2 className="font-mono text-[11px] uppercase tracking-[0.14em] text-muted-foreground">
              Validation pipeline — product example
            </h2>
            <p className="font-mono text-[11px] text-muted-foreground">
              input: <span className="text-foreground/90">{DEMO_INPUT}</span>
            </p>
          </div>
          <ol className="flex flex-col gap-1 sm:flex-row sm:items-start sm:gap-1" aria-label="Validation pipeline example">
            {STAGES.map((stage, i) => (
              <PipelineStage
                key={stage.label}
                stage={stage}
                index={i}
                activeIndex={activeIndex}
                reduceMotion={reduceMotion}
              />
            ))}
          </ol>
          <p className="mt-2 text-[11px] text-muted-foreground">
            Static example shown for illustration — run your own input in the live workspace below.
          </p>
        </div>
      </div>
    </section>
  );
}
