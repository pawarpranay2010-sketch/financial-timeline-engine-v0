"use client";

import Link from "next/link";
import { motion, useReducedMotion } from "framer-motion";
import { ArrowRight, Braces, Layers, ListChecks, Lock } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/**
 * Homepage entry point for the Developer API.
 *
 * Before this section existed, the API was reachable only from the header nav
 * or by knowing /developer directly. This section states what the API IS, what
 * it does (single jobs, bounded multi-transaction batches, job status/result
 * retrieval, tenant isolation) and that access is an invitation-based private
 * beta — the same product truth the /developer page documents.
 */

const CAPABILITIES = [
  {
    icon: Braces,
    title: "Deterministic accounting intelligence",
    body: "The model proposes meaning; strict validation and registry-registered authorities produce the accounting result.",
  },
  {
    icon: Layers,
    title: "Multi-transaction batches",
    body: "Submit a bounded batch as one job — independent transactions, or an ordered chain that carries only verified state forward.",
  },
  {
    icon: ListChecks,
    title: "Job status and results",
    body: "Poll a job, page your job history newest first, and fetch the stored result envelope when it is ready.",
  },
  {
    icon: Lock,
    title: "Tenant-isolated access",
    body: "Every credential is bound to one tenant. No request can read another tenant's jobs, results, or usage.",
  },
] as const;

export function DeveloperCta() {
  const reduceMotion = useReducedMotion();

  return (
    <section className="pb-14" aria-labelledby="build-heading">
      <motion.div
        initial={reduceMotion ? false : { opacity: 0, y: 12 }}
        whileInView={{ opacity: 1, y: 0 }}
        viewport={{ once: true, margin: "-60px" }}
        transition={{ duration: 0.35 }}
        className="rounded-xl border border-accent/40 bg-accent-soft/25 p-5 sm:p-6"
      >
        <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_auto] lg:items-center">
          <div className="min-w-0">
            <h2 id="build-heading" className="text-lg font-semibold tracking-tight">
              Build with Platrixa
            </h2>
            <p className="mt-1.5 max-w-2xl text-sm leading-relaxed text-muted-foreground">
              Integrate deterministic accounting intelligence into your own applications through
              the Platrixa Developer API.
            </p>
          </div>

          <div className="flex flex-col items-start gap-2 lg:items-end">
            <Button asChild>
              <Link href="/developer">
                Explore the Developer API
                <ArrowRight className="ml-1 size-4" aria-hidden />
              </Link>
            </Button>
            <p className="text-[11px] text-muted-foreground lg:text-right">
              Versioned <span className="font-mono text-foreground/90">/v1</span> contract · same
              runtime as this page
            </p>
          </div>
        </div>

        <ul className="mt-5 grid gap-3 sm:grid-cols-2">
          {CAPABILITIES.map((item) => {
            const Icon = item.icon;
            return (
              <li
                key={item.title}
                className={cn(
                  "flex items-start gap-2.5 rounded-lg border border-border/70 bg-card p-3.5",
                )}
              >
                <span className="mt-0.5 grid size-7 shrink-0 place-items-center rounded-md border border-border/70 bg-muted/40 text-accent">
                  <Icon className="size-3.5" aria-hidden />
                </span>
                <div className="min-w-0">
                  <h3 className="text-sm font-medium tracking-tight">{item.title}</h3>
                  <p className="mt-0.5 text-[12.5px] leading-relaxed text-muted-foreground">
                    {item.body}
                  </p>
                </div>
              </li>
            );
          })}
        </ul>

        <p className="mt-4 text-[12.5px] leading-relaxed text-muted-foreground">
          <span className="font-medium text-foreground">Invitation-based private beta.</span>{" "}
          Access is provisioned by the Platrixa team. There is no public signup, no developer
          login, and no self-service key generation — read the developer documentation for the
          onboarding path.
        </p>
      </motion.div>
    </section>
  );
}
