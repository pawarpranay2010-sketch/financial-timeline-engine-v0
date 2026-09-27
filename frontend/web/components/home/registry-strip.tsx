"use client";

import { useEffect, useState } from "react";
import { motion, useReducedMotion } from "framer-motion";
import { fetchCapabilities } from "@/lib/api";
import type { CapabilitiesResponse } from "@/lib/types";

/**
 * Status strip + live registry metrics. The three outcome words introduce
 * the six-state vocabulary; the metrics are computed from the LIVE
 * GET /v1/capabilities response — nothing invented.
 */

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

function StatusTrio() {
  const reduceMotion = useReducedMotion();
  return (
    <div className="grid gap-3 sm:grid-cols-3">
      {STATUS_WORDS.map((s, i) => (
        <motion.p
          key={s.word}
          initial={reduceMotion ? false : { opacity: 0, y: 8 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true, margin: "-40px" }}
          transition={{ delay: reduceMotion ? 0 : i * 0.08, duration: 0.3 }}
          className={`rounded-xl border px-4 py-3.5 ${s.tone}`}
        >
          <span className="block font-mono text-sm font-semibold tracking-tight">{s.word}</span>
          <span className="mt-1 block text-xs opacity-80">{s.note}</span>
        </motion.p>
      ))}
    </div>
  );
}

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
  const supported = data.capabilities.filter((c) => c.supported_status === "SUPPORTED").length;
  const partial = data.capabilities.filter((c) => c.supported_status === "PARTIAL").length;
  const unsupported = data.capabilities.filter((c) => c.supported_status === "UNSUPPORTED").length;
  const planned = data.count - supported - partial - unsupported;
  const authorities = new Set(data.capabilities.map((c) => c.authority)).size;

  return (
    <div className="mt-8">
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
          ["authorities", authorities],
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

export function RegistryStrip() {
  return (
    <section className="pb-14" aria-label="Outcome states and live registry">
      <StatusTrio />
      <RegistryMetrics />
    </section>
  );
}
