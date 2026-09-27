"use client";

import { motion, useReducedMotion } from "framer-motion";
import { ArrowDown, Braces, Landmark, ShieldCheck } from "lucide-react";
import { cn } from "@/lib/utils";

/**
 * Why Platrixa — the three-step architecture explanation. One glance
 * should communicate the separation of powers: the model proposes,
 * Platrixa validates, authorities execute.
 */

const STEPS = [
  {
    icon: Braces,
    title: "AI understands meaning",
    body: "A language model reads messy financial language and proposes a structured interpretation. It is fast, flexible — and untrusted.",
    tone: "text-foreground",
    ring: "border-border/70",
  },
  {
    icon: ShieldCheck,
    title: "Platrixa validates meaning",
    body: "Strict schemas, evidence grounding, conflict checks, and capability lookup decide whether the interpretation can be trusted — or must fail closed.",
    tone: "text-accent",
    ring: "border-accent/40 bg-accent-soft/40",
  },
  {
    icon: Landmark,
    title: "Authorities calculate and execute",
    body: "Registry-registered deterministic authorities produce the accounting result. The same input and registry state always produce the same result.",
    tone: "text-status-verified",
    ring: "border-status-verified/35 bg-status-verified/5",
  },
] as const;

export function WhyPlatrixa() {
  const reduceMotion = useReducedMotion();

  return (
    <section className="pb-14" aria-labelledby="why-heading">
      <div className="mb-5">
        <h2 id="why-heading" className="text-lg font-semibold tracking-tight">
          Why Platrixa
        </h2>
        <p className="mt-0.5 max-w-2xl text-sm text-muted-foreground">
          The architecture in three steps — the model proposes, the runtime disposes.
        </p>
      </div>

      <ol className="grid gap-3 md:grid-cols-3">
        {STEPS.map((step, i) => {
          const Icon = step.icon;
          return (
            <motion.li
              key={step.title}
              initial={reduceMotion ? false : { opacity: 0, y: 12 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true, margin: "-60px" }}
              transition={{ delay: reduceMotion ? 0 : i * 0.12, duration: 0.35 }}
              className="min-w-0"
            >
              <div className={cn("h-full rounded-xl border p-5", step.ring)}>
                <div className="flex items-center gap-2.5">
                  <span className={cn("grid size-8 place-items-center rounded-lg border border-border/70 bg-card", step.tone)}>
                    <Icon className="size-4" aria-hidden />
                  </span>
                  <p className="font-mono text-[11px] tracking-[0.12em] text-muted-foreground">
                    {String(i + 1).padStart(2, "0")}
                  </p>
                </div>
                <h3 className={cn("mt-3 text-base font-semibold tracking-tight", step.tone)}>{step.title}</h3>
                <p className="mt-1.5 text-sm leading-relaxed text-muted-foreground">{step.body}</p>
              </div>
              {i < STEPS.length - 1 && (
                <div className="mt-2 flex justify-center" aria-hidden>
                  {/* vertical flow on mobile, left→right flow on md+ */}
                  <ArrowDown className="size-4 text-muted-foreground/60 md:rotate-[-90deg]" />
                </div>
              )}
              {i === STEPS.length - 1 && (
                <p className="mt-2 text-center font-mono text-[11px] text-muted-foreground">
                  the model never computes the result
                </p>
              )}
            </motion.li>
          );
        })}
      </ol>
    </section>
  );
}
