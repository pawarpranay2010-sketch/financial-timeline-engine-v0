"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import { ArrowDown, ArrowRight } from "lucide-react";
import { Button } from "@/components/ui/button";
import { StatusBadge } from "@/components/status";
import { cn } from "@/lib/utils";

/**
 * Developer flow — an animated request → validation → response console
 * showing the REAL Phase 5D result contract (fields as returned by
 * POST /v1/process). The animation illustrates the product flow; the
 * payload shown is a documented contract example, not a live network call.
 */

const REQUEST_LINES = [
  'curl -X POST "$PLATRIXA_HOST/v1/process" \\',
  '  -H "X-Platrixa-API-Key: YOUR_API_KEY" \\',
  '  -H "Idempotency-Key: YOUR_IDEMPOTENCY_KEY" \\',
  '  -H "Content-Type: application/json" \\',
  "  -d '{\"raw_input\": \"Purchased furniture for cash Rs. 15,000\"}'",
];

const RESPONSE_FIELDS: Array<{ key: string; value: string; tone?: "verified" | "accent" | "muted" }> = [
  { key: '"request_id"', value: '"req-4f2a91c7"', tone: "muted" },
  { key: '"status"', value: '"VERIFIED"', tone: "verified" },
  { key: '"api_status"', value: '"VERIFIED"', tone: "verified" },
  { key: '"success"', value: "true", tone: "verified" },
  { key: '"retryable"', value: "false", tone: "muted" },
  { key: '"reason_codes"', value: "[]", tone: "muted" },
  { key: '"interpretation"', value: "{ transaction_type: PURCHASE, … }", tone: "accent" },
  { key: '"evidence"', value: "[ { page, text_span, … } ]", tone: "accent" },
  { key: '"accounting_result"', value: "{ debit_lines, credit_lines, … }", tone: "verified" },
  { key: '"metadata"', value: "{ engine_status, processing_time_ms, … }", tone: "muted" },
];

const PHASE_MS = 1900;

type FlowPhase = 0 | 1 | 2; // request → validation → response

function useFlowPhase(reduceMotion: boolean | null): FlowPhase {
  const [tick, setTick] = useState<FlowPhase>(0);
  useEffect(() => {
    if (reduceMotion) return; // static full view — no loop, no timer
    const timer = window.setInterval(() => {
      setTick((p) => ((p + 1) % 3) as FlowPhase);
    }, PHASE_MS);
    return () => window.clearInterval(timer);
  }, [reduceMotion]);
  return reduceMotion ? 2 : tick;
}

export function DeveloperFlow() {
  const reduceMotion = useReducedMotion();
  const phase = useFlowPhase(reduceMotion);

  return (
    <section className="pb-14" aria-labelledby="dev-heading">
      <div className="mb-5">
        <h2 id="dev-heading" className="text-lg font-semibold tracking-tight">
          Built for developers
        </h2>
        <p className="mt-0.5 max-w-2xl text-sm text-muted-foreground">
          The homepage workspace is a thin view over the versioned{" "}
          <span className="font-mono text-[13px] text-foreground/90">/v1</span> API. Same runtime,
          same six-state contract, same evidence — deterministic and replay-safe.
        </p>
      </div>

      <div className="grid gap-3 lg:grid-cols-[1fr_auto_1fr] lg:items-start">
        {/* Request */}
        <div
          className={cn(
            "rounded-xl border p-4 transition-colors duration-300",
            phase === 0 ? "border-accent/50 bg-accent-soft/30" : "border-border/70 bg-card",
          )}
          aria-label="API request example"
        >
          <div className="flex items-center justify-between gap-2">
            <p className="font-mono text-[10.5px] uppercase tracking-[0.14em] text-muted-foreground">Request</p>
            <p className="font-mono text-[10.5px] text-muted-foreground">POST /v1/process</p>
          </div>
          <pre className="mt-3 overflow-x-auto rounded-lg border border-border/60 bg-muted/40 p-3 font-mono text-[11.5px] leading-relaxed">
            {REQUEST_LINES.map((line, i) => (
              <motion.span
                key={i}
                initial={reduceMotion ? false : { opacity: 0 }}
                animate={{ opacity: phase >= 0 ? 1 : 0.35 }}
                transition={{ delay: i * 0.04, duration: 0.2 }}
                className="block whitespace-pre"
              >
                {line}
              </motion.span>
            ))}
          </pre>
          <p className="mt-2.5 text-[11px] leading-relaxed text-muted-foreground">
            Your key authenticates the request; the idempotency key makes retries safe.
          </p>
        </div>

        {/* Validation connector */}
        <div className="flex items-center justify-center py-2 lg:min-h-[220px] lg:flex-col lg:py-0" aria-hidden>
          <motion.span
            animate={reduceMotion ? {} : { x: [0, 6, 0], y: [0, -6, 0] }}
            transition={{ repeat: Infinity, duration: 1.6, ease: "easeInOut" }}
          >
            <ArrowRight className="size-5 text-accent lg:hidden" />
            <ArrowDown className="hidden size-5 text-accent lg:inline-block" />
          </motion.span>
          <span className="ml-2 font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground lg:ml-0 lg:mt-2">
            {phase === 1 ? "validating…" : "deterministic runtime"}
          </span>
        </div>

        {/* Response */}
        <div
          className={cn(
            "rounded-xl border p-4 transition-colors duration-300",
            phase === 2 ? "border-status-verified/40 bg-status-verified/5" : "border-border/70 bg-card",
          )}
          aria-label="API response example — Phase 5D result contract"
        >
          <div className="flex items-center justify-between gap-2">
            <p className="font-mono text-[10.5px] uppercase tracking-[0.14em] text-muted-foreground">Response</p>
            <AnimatePresence mode="wait" initial={false}>
              {phase === 1 ? (
                <motion.span
                  key="validating"
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                  exit={{ opacity: 0 }}
                  className="font-mono text-[10.5px] text-accent"
                >
                  validating…
                </motion.span>
              ) : (
                <motion.span key="done" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
                  <StatusBadge status="VERIFIED" label="200 VERIFIED" size="sm" />
                </motion.span>
              )}
            </AnimatePresence>
          </div>

          <pre className="mt-3 overflow-x-auto rounded-lg border border-border/60 bg-muted/40 p-3 font-mono text-[11.5px] leading-relaxed">
            <span className="block text-muted-foreground">{"{"}</span>
            {RESPONSE_FIELDS.map((field, i) => (
              <motion.span
                key={field.key}
                initial={reduceMotion ? false : { opacity: 0, x: -4 }}
                animate={{
                  opacity: phase === 2 ? 1 : 0.3,
                  x: 0,
                }}
                transition={{ delay: phase === 2 ? i * 0.05 : 0, duration: 0.22 }}
                className={cn(
                  "block whitespace-pre",
                  field.tone === "verified" && "text-status-verified",
                  field.tone === "accent" && "text-accent",
                )}
              >
                {"  "}
                {field.key}: {field.value}
              </motion.span>
            ))}
            <span className="block text-muted-foreground">{"}"}</span>
          </pre>
          <p className="mt-2.5 text-[11px] leading-relaxed text-muted-foreground">
            Phase 5D result contract — the exact envelope the workspace above renders, sync and
            async.
          </p>
        </div>
      </div>

      <div className="mt-4 flex flex-wrap items-center gap-3">
        <Button asChild variant="outline">
          <Link href="/developer">
            Read developer documentation
            <ArrowRight className="ml-1 size-4" aria-hidden />
          </Link>
        </Button>
        <p className="text-[11px] text-muted-foreground">
          Contract example — six states, structured errors, capability discovery, async jobs.
        </p>
      </div>
    </section>
  );
}
