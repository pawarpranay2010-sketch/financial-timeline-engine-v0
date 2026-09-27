"use client";

import Link from "next/link";
import { motion, useReducedMotion } from "framer-motion";
import { Check, X } from "lucide-react";

/**
 * Trust & Safety — concise product-oriented trust section mirroring the
 * /trust page wording. Two columns with icon + text (never color alone),
 * plus the precise VERIFIED semantics.
 */

const DOES = [
  "Validates supported financial semantics",
  "Checks evidence and consistency",
  "Routes supported operations to deterministic authorities",
  "Exposes uncertainty explicitly",
  "Provides review paths for ambiguous inputs",
  "Preserves request and evidence lineage where available",
] as const;

const DOES_NOT = [
  "Guarantee factual truth of source information",
  "Guarantee tax compliance",
  "Guarantee legal compliance",
  "Provide investment advice",
  "Replace professional judgment",
  "Invent unsupported financial conclusions",
] as const;

export function TrustSafetySection() {
  const reduceMotion = useReducedMotion();

  return (
    <section className="pb-14" aria-labelledby="trust-heading">
      <div className="mb-5">
        <h2 id="trust-heading" className="text-lg font-semibold tracking-tight">
          Trust &amp; Safety
        </h2>
        <p className="mt-0.5 max-w-2xl text-sm text-muted-foreground">
          Validation infrastructure, not an adviser — and explicit about the difference.
        </p>
      </div>

      <div className="grid gap-3 md:grid-cols-2">
        <motion.div
          initial={reduceMotion ? false : { opacity: 0, y: 10 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true, margin: "-60px" }}
          transition={{ duration: 0.3 }}
          className="rounded-xl border border-status-verified/35 bg-status-verified/5 p-5"
        >
          <h3 className="font-mono text-[11px] font-semibold uppercase tracking-[0.14em] text-status-verified">
            What Platrixa does
          </h3>
          <ul className="mt-3 space-y-2">
            {DOES.map((item) => (
              <li key={item} className="flex items-start gap-2.5 text-sm text-foreground/90">
                <Check className="mt-0.5 size-4 shrink-0 text-status-verified" aria-hidden />
                {item}
              </li>
            ))}
          </ul>
        </motion.div>

        <motion.div
          initial={reduceMotion ? false : { opacity: 0, y: 10 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true, margin: "-60px" }}
          transition={{ duration: 0.3, delay: reduceMotion ? 0 : 0.08 }}
          className="rounded-xl border border-border/70 bg-card p-5"
        >
          <h3 className="font-mono text-[11px] font-semibold uppercase tracking-[0.14em] text-muted-foreground">
            What Platrixa does not do
          </h3>
          <ul className="mt-3 space-y-2">
            {DOES_NOT.map((item) => (
              <li key={item} className="flex items-start gap-2.5 text-sm text-muted-foreground">
                <X className="mt-0.5 size-4 shrink-0 text-muted-foreground/70" aria-hidden />
                {item}
              </li>
            ))}
          </ul>
        </motion.div>
      </div>

      <div className="mt-3 rounded-xl border border-border/70 bg-card p-5">
        <p className="text-sm leading-relaxed text-muted-foreground">
          <span className="font-mono font-semibold text-status-verified">VERIFIED</span> means the
          input satisfied Platrixa&apos;s implemented validation, grounding, capability, and
          deterministic-authority requirements. It does <strong className="text-foreground">not</strong>{" "}
          mean legally compliant, tax compliant, factually guaranteed, or financially advisable.
          Full details on{" "}
          <Link href="/trust" className="text-accent hover:underline">
            Trust &amp; Safety
          </Link>
          .
        </p>
      </div>
    </section>
  );
}
