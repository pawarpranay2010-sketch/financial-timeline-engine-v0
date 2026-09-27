"use client";

import { motion, useReducedMotion } from "framer-motion";
import { ShieldQuestion, SplitSquareHorizontal } from "lucide-react";
import { StatusBadge } from "@/components/status";

/**
 * Failure modes — REVIEW_REQUIRED and UNSUPPORTED presented as designed
 * safety states, not errors. Both examples are clearly-labeled product
 * examples, not live results.
 */

const MODES = [
  {
    icon: SplitSquareHorizontal,
    badge: "REVIEW_REQUIRED" as const,
    title: "Conflicting evidence",
    rows: [
      ["Subtotal", "Rs. 10,000"],
      ["Tax", "Rs. 5,000"],
      ["Total", "Rs. 12,000"],
    ],
    conflict: "10,000 + 5,000 ≠ 12,000 — these values do not reconcile.",
    explanation:
      "The runtime records the discrepancy and flags the input for review. It does not pick a number, average the totals, or invent the missing correction.",
    ring: "border-status-review/40 bg-status-review/5",
    iconTone: "text-status-review",
  },
  {
    icon: ShieldQuestion,
    badge: "UNSUPPORTED" as const,
    title: "Unsupported capability",
    input: "Model a portfolio hedge using an interest-rate swap",
    explanation:
      "No registered capability covers this operation, so no authority accepts it. The system refuses explicitly and records the boundary instead of improvising an answer.",
    ring: "border-status-unsupported/40 bg-status-unsupported/5",
    iconTone: "text-status-unsupported",
  },
] as const;

export function FailureModes() {
  const reduceMotion = useReducedMotion();

  return (
    <section className="pb-14" aria-labelledby="failure-heading">
      <div className="mb-5">
        <h2 id="failure-heading" className="text-lg font-semibold tracking-tight">
          Failure is part of the product
        </h2>
        <p className="mt-0.5 max-w-2xl text-sm text-muted-foreground">
          When evidence or capability is missing, Platrixa fails closed. These are safety states,
          not errors.
        </p>
      </div>

      <div className="grid gap-3 md:grid-cols-2">
        {MODES.map((mode, i) => {
          const Icon = mode.icon;
          return (
            <motion.article
              key={mode.title}
              initial={reduceMotion ? false : { opacity: 0, y: 12 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true, margin: "-60px" }}
              transition={{ delay: reduceMotion ? 0 : i * 0.12, duration: 0.35 }}
              className={mode.ring + " rounded-xl border p-5"}
            >
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span className="flex items-center gap-2 text-sm font-semibold">
                  <Icon className={"size-4 " + mode.iconTone} aria-hidden />
                  {mode.title}
                </span>
                <StatusBadge status={mode.badge} label={mode.badge} size="sm" />
              </div>

              <div className="mt-4 rounded-lg border border-border/70 bg-card px-4 py-3">
                {"rows" in mode ? (
                  <dl className="space-y-1.5">
                    {mode.rows.map(([label, value]) => (
                      <div key={label} className="flex items-baseline justify-between gap-4">
                        <dt className="font-mono text-[12px] text-muted-foreground">{label}</dt>
                        <dd className="font-mono text-[13px] text-foreground">{value}</dd>
                      </div>
                    ))}
                    <p className="border-t border-border/60 pt-2 font-mono text-[12px] text-status-review">
                      {mode.conflict}
                    </p>
                  </dl>
                ) : (
                  <p className="font-mono text-[13px] text-foreground/90">&ldquo;{mode.input}&rdquo;</p>
                )}
              </div>

              <p className="mt-3 text-sm leading-relaxed text-muted-foreground">{mode.explanation}</p>
            </motion.article>
          );
        })}
      </div>

      <p className="mt-3 text-[11px] text-muted-foreground">
        Illustrative examples of documented runtime behavior — the status contract is defined by the
        backend, never by this UI.
      </p>
    </section>
  );
}
