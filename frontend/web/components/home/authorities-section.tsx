"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { motion, useReducedMotion } from "framer-motion";
import { ArrowRight, Landmark } from "lucide-react";
import { Button } from "@/components/ui/button";
import { fetchCapabilities } from "@/lib/api";
import type { CapabilitiesResponse } from "@/lib/types";
import { cn } from "@/lib/utils";

/**
 * Authorities — the three deterministic authority families. Counts and
 * example capabilities come from the LIVE registry (GET /v1/capabilities);
 * the full list is never duplicated here, the registry page is the source
 * of truth.
 */

const FAMILIES = [
  {
    id: "ACCOUNTING_KERNEL",
    match: "ACCOUNT",
    title: "Accounting Kernel",
    body: "The deterministic journal-entry engine for supported book-keeping transactions — executes only what validated semantics and grounding support.",
  },
  {
    id: "FORMULA_AUTHORITY",
    match: "FORMULA",
    title: "Formula Authority",
    body: "Deterministic financial ratios and formulas — each a registered capability with an implementation reference and a proving test.",
  },
  {
    id: "FINANCE_KNOWLEDGE",
    match: "KNOWLEDGE",
    title: "Finance Knowledge",
    body: "Versioned finance-knowledge records with explicit sources — terminology served from records, not generated.",
  },
] as const;

function familyStats(data: CapabilitiesResponse, match: string) {
  const caps = data.capabilities.filter((c) => (c.authority ?? "").toUpperCase().includes(match));
  const supported = caps.filter((c) => c.supported_status === "SUPPORTED");
  const examples = (supported.length >= 2 ? supported : caps).slice(0, 2);
  return { total: caps.length, supported: supported.length, examples };
}

export function AuthoritiesSection() {
  const reduceMotion = useReducedMotion();
  const [data, setData] = useState<CapabilitiesResponse | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    fetchCapabilities(controller.signal)
      .then(setData)
      .catch(() => setFailed(true));
    return () => controller.abort();
  }, []);

  return (
    <section className="pb-14" aria-labelledby="authorities-heading">
      <div className="mb-5">
        <h2 id="authorities-heading" className="text-lg font-semibold tracking-tight">
          Three deterministic authorities
        </h2>
        <p className="mt-0.5 max-w-2xl text-sm text-muted-foreground">
          Every journal entry and every formula comes from a registry-registered authority. Counts
          below are the live registry, not marketing.
        </p>
      </div>

      <div className="grid gap-3 md:grid-cols-3">
        {FAMILIES.map((family, i) => {
          const stats = data ? familyStats(data, family.match) : null;
          return (
            <motion.article
              key={family.id}
              initial={reduceMotion ? false : { opacity: 0, y: 12 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true, margin: "-60px" }}
              transition={{ delay: reduceMotion ? 0 : i * 0.1, duration: 0.35 }}
              className="flex h-full flex-col rounded-xl border border-border/70 bg-card p-5 transition-colors hover:border-accent/30"
            >
              <div className="flex items-center gap-2.5">
                <span className="grid size-8 place-items-center rounded-lg border border-border/70 bg-muted/40 text-accent">
                  <Landmark className="size-4" aria-hidden />
                </span>
                <div className="min-w-0">
                  <h3 className="text-sm font-semibold tracking-tight">{family.title}</h3>
                  <p className="truncate font-mono text-[10px] uppercase tracking-[0.12em] text-muted-foreground">
                    {family.id}
                  </p>
                </div>
              </div>

              <p className="mt-3 text-sm leading-relaxed text-muted-foreground">{family.body}</p>

              <div className="mt-4 space-y-1.5 border-t border-border/60 pt-3">
                {stats ? (
                  <>
                    <p className="font-mono text-[11px] text-muted-foreground">
                      <span className="font-semibold text-foreground">{stats.supported}</span> supported
                      {" · "}
                      <span className="font-semibold text-foreground">{stats.total}</span> in registry
                    </p>
                    {stats.examples.map((cap) => (
                      <p key={cap.capability_id} className="truncate font-mono text-[11px] text-foreground/85" title={cap.canonical_name}>
                        <span
                          className={cn(
                            "mr-1.5 inline-block size-1.5 rounded-full align-middle",
                            cap.supported_status === "SUPPORTED" ? "bg-status-verified" : "bg-status-review",
                          )}
                          aria-hidden
                        />
                        {cap.canonical_name}
                      </p>
                    ))}
                  </>
                ) : (
                  <p className="font-mono text-[11px] text-muted-foreground">
                    {failed ? "registry unavailable — counts hidden, not guessed" : "loading live registry…"}
                  </p>
                )}
              </div>
            </motion.article>
          );
        })}
      </div>

      <div className="mt-4">
        <Button asChild variant="outline">
          <Link href="/capabilities">
            Explore capability registry
            <ArrowRight className="ml-1 size-4" aria-hidden />
          </Link>
        </Button>
      </div>
    </section>
  );
}
