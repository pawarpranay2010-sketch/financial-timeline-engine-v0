"use client";

import { motion, useReducedMotion } from "framer-motion";
import { Check, CircleDashed, LoaderCircle, X } from "lucide-react";
import type { Phase } from "@/components/workspace";
import { cn } from "@/lib/utils";

/**
 * Validation progress — a truthful phase timeline for the live workspace.
 *
 * It renders the four phases the frontend actually knows about:
 *   Request submitted → Engine processing → Response received → Result rendered
 *
 * It does NOT fabricate backend-internal stages (schema, grounding, …) as
 * if the API streamed them — those are shown as the conceptual journey in
 * the Validation Journey section instead.
 */

const STEPS = [
  { label: "Request submitted", note: "input leaves the browser" },
  { label: "Engine processing", note: "interpretation → schema → grounding → authority" },
  { label: "Response received", note: "terminal state decided by the runtime" },
  { label: "Result rendered", note: "outcome displayed with its evidence" },
] as const;

/** Highest step fully reached (0-based); -1 before anything is submitted. */
function reachedStep(phase: Phase): number {
  switch (phase) {
    case "idle":
      return -1;
    case "loading":
      return 1; // submitted + processing in flight
    case "done":
      return 3; // everything completed
    case "error":
      return 2; // response arrived (an error response), rendering stopped
  }
}

export function ValidationProgress({ phase }: { phase: Phase }) {
  const reduceMotion = useReducedMotion();
  const reached = reachedStep(phase);
  const failed = phase === "error";

  return (
    <section aria-label="Validation progress" className="rounded-xl border border-border/70 bg-card p-4">
      <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1">
        <h3 className="font-mono text-[11px] uppercase tracking-[0.14em] text-muted-foreground">
          Validation progress
        </h3>
        <p className="font-mono text-[11px] text-muted-foreground" role="status">
          {phase === "idle" && "waiting for input"}
          {phase === "loading" && "request in flight…"}
          {phase === "done" && "response rendered below"}
          {phase === "error" && "request failed — see error panel"}
        </p>
      </div>

      <ol className="mt-3 flex flex-col gap-2 sm:flex-row sm:items-center sm:gap-0">
        {STEPS.map((step, i) => {
          const isFailedTerminal = failed && i === 3;
          const isReached = i <= reached;
          const isProcessing = phase === "loading" && i === 1;
          const Icon = isFailedTerminal ? X : isReached ? Check : isProcessing ? LoaderCircle : CircleDashed;
          return (
            <li key={step.label} className="flex items-center gap-2.5 sm:flex-1 sm:gap-0">
              <motion.span
                initial={reduceMotion ? false : { opacity: 0, scale: 0.85 }}
                animate={{ opacity: 1, scale: 1 }}
                transition={{ duration: 0.2 }}
                className={cn(
                  "grid size-6 shrink-0 place-items-center rounded-full border",
                  isFailedTerminal
                    ? "border-status-unsupported/50 bg-status-unsupported/10 text-status-unsupported"
                    : isReached
                      ? "border-status-verified/50 bg-status-verified/10 text-status-verified"
                      : isProcessing
                        ? "border-accent/60 bg-accent-soft text-accent"
                        : "border-border bg-muted/40 text-muted-foreground",
                )}
              >
                <Icon className={cn("size-3.5", isProcessing && !reduceMotion && "animate-spin")} aria-hidden />
              </motion.span>
              <span className="min-w-0">
                <span
                  className={cn(
                    "block truncate text-xs font-medium",
                    isReached || isProcessing ? "text-foreground" : "text-muted-foreground",
                  )}
                >
                  {step.label}
                </span>
                <span className="hidden truncate text-[10.5px] text-muted-foreground lg:block">{step.note}</span>
              </span>
              {i < STEPS.length - 1 && <span aria-hidden className="mx-2 hidden h-px min-w-2 flex-1 bg-border sm:block" />}
            </li>
          );
        })}
      </ol>
    </section>
  );
}
