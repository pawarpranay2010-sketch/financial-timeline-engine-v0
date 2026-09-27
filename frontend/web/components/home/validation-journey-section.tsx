"use client";

import { useEffect, useRef, useState } from "react";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import {
  Braces,
  Check,
  ChevronsRight,
  FileText,
  Landmark,
  ScanSearch,
  ShieldCheck,
} from "lucide-react";
import { StatusBadge } from "@/components/status";
import { cn } from "@/lib/utils";

/**
 * Validation journey — an interactive walk of ONE transaction through the
 * six conceptual stages of the Platrixa pipeline.
 *
 * Honesty rules:
 *  - The walkthrough is a labeled product example, not a live response.
 *  - IR fields the input does not state are shown as "Not stated" — never
 *    inferred.
 *  - Reduced motion disables the auto-advance loop entirely; user
 *    interaction pauses it permanently for the session of the page view.
 */

const DEMO_INPUT = "Purchased furniture for cash Rs. 15,000";
const AUTO_ADVANCE_MS = 4800; // enough dwell to scan each stage's panel

const STAGES = [
  { id: "input", label: "Input", icon: FileText },
  { id: "semantic-ir", label: "Semantic IR", icon: Braces },
  { id: "evidence", label: "Evidence", icon: ScanSearch },
  { id: "grounding", label: "Grounding", icon: ShieldCheck },
  { id: "authority", label: "Authority", icon: Landmark },
  { id: "result", label: "Result", icon: Check },
] as const;

type StageId = (typeof STAGES)[number]["id"];

/* ------------------------------- stage data ------------------------------ */

const IR_FIELDS: Array<{ field: string; value: string; stated: boolean }> = [
  { field: "transaction_type", value: "PURCHASE", stated: true },
  { field: "item", value: "Furniture", stated: true },
  { field: "payment_method", value: "CASH", stated: true },
  { field: "amount", value: "Rs. 15,000", stated: true },
  { field: "date", value: "Not stated", stated: false },
  { field: "references", value: "Not stated", stated: false },
];

const EVIDENCE_ITEMS = [
  { token: "Purchased furniture", note: "asset purchase language" },
  { token: "cash", note: "explicit payment instrument" },
  { token: "Rs. 15,000", note: "explicit amount" },
];

const GROUNDING_CHECKS = [
  { label: "Schema", state: "pass" as const, note: "required fields present" },
  { label: "Evidence", state: "pass" as const, note: "claims tied to input" },
  { label: "Conflict", state: "none" as const, note: "no contradictions found" },
  { label: "Capability", state: "pass" as const, note: "kernel supports PURCHASE" },
];

/* ------------------------------ stage panels ----------------------------- */

function PanelShell({ children }: { children: React.ReactNode }) {
  return <div className="rounded-xl border border-border/70 bg-card p-5 sm:p-6">{children}</div>;
}

function InputPanel() {
  return (
    <PanelShell>
      <p className="font-mono text-[10.5px] uppercase tracking-[0.14em] text-muted-foreground">Stage 01 — raw input</p>
      <blockquote className="mt-3 rounded-lg border border-accent/30 bg-accent-soft/50 px-4 py-3.5 font-mono text-sm leading-relaxed text-foreground">
        &ldquo;{DEMO_INPUT}&rdquo;
      </blockquote>
      <p className="mt-3 text-sm leading-relaxed text-muted-foreground">
        Raw financial language enters Platrixa. Nothing is normalized, paraphrased, or completed —
        the model must work with exactly what was written, and every downstream claim must trace
        back to this text.
      </p>
    </PanelShell>
  );
}

function SemanticIrPanel() {
  return (
    <PanelShell>
      <p className="font-mono text-[10.5px] uppercase tracking-[0.14em] text-muted-foreground">
        Stage 02 — candidate semantic IR (model-proposed)
      </p>
      <dl className="mt-3 grid gap-x-6 gap-y-2.5 sm:grid-cols-2">
        {IR_FIELDS.map((f) => (
          <div key={f.field} className="flex items-baseline justify-between gap-3 border-b border-border/50 pb-2">
            <dt className="font-mono text-[11px] uppercase tracking-wide text-muted-foreground">{f.field}</dt>
            <dd
              className={cn(
                "font-mono text-[13px]",
                f.stated ? "font-medium text-foreground" : "italic text-muted-foreground",
              )}
            >
              {f.value}
            </dd>
          </div>
        ))}
      </dl>
      <p className="mt-3 text-sm leading-relaxed text-muted-foreground">
        The model proposes an 18-field structured candidate. Fields the input does not state are
        displayed as <span className="font-mono text-[12px] italic">Not stated</span> — Platrixa
        never infers missing values. This candidate is a proposal only: it decides nothing.
      </p>
    </PanelShell>
  );
}

function EvidencePanel() {
  return (
    <PanelShell>
      <p className="font-mono text-[10.5px] uppercase tracking-[0.14em] text-muted-foreground">Stage 03 — evidence</p>
      <div className="mt-3 rounded-lg border border-border/70 bg-muted/30 px-4 py-3">
        <p className="text-[10.5px] uppercase tracking-[0.14em] text-muted-foreground">Source</p>
        <p className="mt-1 font-mono text-sm text-foreground/90">&ldquo;{DEMO_INPUT}&rdquo;</p>
      </div>
      <ul className="mt-3 flex flex-wrap gap-2" aria-label="Evidence extracted from the input">
        {EVIDENCE_ITEMS.map((item, i) => (
          <motion.li
            key={item.token}
            initial={{ opacity: 0, scale: 0.94 }}
            animate={{ opacity: 1, scale: 1 }}
            transition={{ delay: 0.1 + i * 0.08, duration: 0.22 }}
            className="rounded-lg border border-accent/30 bg-accent-soft px-3 py-1.5"
          >
            <span className="block font-mono text-[12.5px] font-medium text-accent">{item.token}</span>
            <span className="block text-[10.5px] text-muted-foreground">{item.note}</span>
          </motion.li>
        ))}
      </ul>
      <p className="mt-3 text-sm leading-relaxed text-muted-foreground">
        Every claim in the candidate is tied back to explicit evidence in the source text. Claims
        without evidence fail grounding — they are never silently accepted.
      </p>
    </PanelShell>
  );
}

function GroundingPanel() {
  return (
    <PanelShell>
      <p className="font-mono text-[10.5px] uppercase tracking-[0.14em] text-muted-foreground">
        Stage 04 — grounding &amp; consistency checks
      </p>
      <ul className="mt-3 grid gap-2 sm:grid-cols-2">
        {GROUNDING_CHECKS.map((c) => (
          <li key={c.label} className="flex items-start gap-2.5 rounded-lg border border-border/70 px-3.5 py-2.5">
            {c.state === "pass" ? (
              <Check className="mt-0.5 size-4 shrink-0 text-status-verified" aria-hidden />
            ) : (
              <span className="mt-1.5 size-2 shrink-0 rounded-full bg-muted-foreground/50" aria-hidden />
            )}
            <span>
              <span className="block text-sm font-medium">{c.label}</span>
              <span className="block text-[11.5px] text-muted-foreground">{c.note}</span>
            </span>
            <span className="sr-only"> — {c.state === "pass" ? "passed" : "no conflicts"}</span>
          </li>
        ))}
      </ul>
      <p className="mt-3 text-sm leading-relaxed text-muted-foreground">
        Schema shape, evidence coverage, internal contradictions, and capability coverage are
        checked deterministically. A single failed check stops the pipeline here.
      </p>
    </PanelShell>
  );
}

function AuthorityPanel() {
  return (
    <PanelShell>
      <p className="font-mono text-[10.5px] uppercase tracking-[0.14em] text-muted-foreground">
        Stage 05 — capability routing &amp; authority
      </p>
      <div className="mt-3 space-y-1.5">
        <div className="rounded-lg border border-border/70 bg-muted/30 px-4 py-2.5 font-mono text-[13px]">
          ACCOUNTING_KERNEL
        </div>
        <div className="flex justify-center" aria-hidden>
          <span className="h-5 w-px bg-accent/50" />
        </div>
        <div className="rounded-lg border border-accent/40 bg-accent-soft px-4 py-2.5 font-mono text-[13px] text-accent">
          deterministic authority
        </div>
        <div className="flex justify-center" aria-hidden>
          <span className="h-5 w-px bg-accent/50" />
        </div>
        <div className="rounded-lg border border-status-verified/40 bg-status-verified/10 px-4 py-2.5 font-mono text-[13px] text-status-verified">
          transaction execution — journal entry
        </div>
      </div>
      <p className="mt-3 text-sm leading-relaxed text-muted-foreground">
        Validated semantics are routed through the capability registry to a registered,
        deterministic authority. The model never touches this step — accounting is computed, not
        generated.
      </p>
    </PanelShell>
  );
}

function ResultPanel() {
  return (
    <PanelShell>
      <p className="font-mono text-[10.5px] uppercase tracking-[0.14em] text-muted-foreground">Stage 06 — decision</p>
      <div className="mt-3 flex flex-wrap items-center gap-3">
        <StatusBadge status="VERIFIED" label="VERIFIED" size="lg" />
        <p className="text-sm text-muted-foreground">
          Why: explicit amount, explicit payment method, evidence grounded, and the accounting
          kernel supports this operation.
        </p>
      </div>
      <div className="mt-4 overflow-hidden rounded-lg border border-border/70">
        <table className="w-full text-sm">
          <caption className="sr-only">Deterministic accounting result for the example transaction</caption>
          <tbody>
            <tr className="border-b border-border/60 bg-muted/20">
              <th scope="row" className="px-3.5 py-2 text-left font-mono text-[12.5px] font-medium">Furniture</th>
              <td className="px-3.5 py-2 text-right font-mono text-[10.5px] uppercase tracking-wider text-muted-foreground">debit</td>
              <td className="px-3.5 py-2 text-right font-mono text-[12.5px]">15,000</td>
            </tr>
            <tr>
              <th scope="row" className="px-3.5 py-2 text-left font-mono text-[12.5px] font-medium">Cash</th>
              <td className="px-3.5 py-2 text-right font-mono text-[10.5px] uppercase tracking-wider text-muted-foreground">credit</td>
              <td className="px-3.5 py-2 text-right font-mono text-[12.5px]">15,000</td>
            </tr>
          </tbody>
        </table>
      </div>
      <p className="mt-3 text-xs leading-relaxed text-muted-foreground">
        VERIFIED means the example satisfied the implemented validation, grounding, capability, and
        authority requirements. It does not mean legal, tax, or factual compliance — see{" "}
        <a href="/trust" className="text-accent hover:underline">Trust &amp; Safety</a>.
      </p>
    </PanelShell>
  );
}

const PANELS: Record<StageId, () => React.ReactElement> = {
  input: InputPanel,
  "semantic-ir": SemanticIrPanel,
  evidence: EvidencePanel,
  grounding: GroundingPanel,
  authority: AuthorityPanel,
  result: ResultPanel,
};

/* ------------------------------ interaction ------------------------------ */

export function ValidationJourneySection() {
  const reduceMotion = useReducedMotion();
  const [active, setActive] = useState(0);
  const [autoPlaying, setAutoPlaying] = useState(true);
  const interacted = useRef(false);

  useEffect(() => {
    if (!autoPlaying || reduceMotion) return;
    if (typeof document !== "undefined" && document.hidden) return;
    const timer = window.setInterval(() => {
      setActive((a) => (a + 1) % STAGES.length);
    }, AUTO_ADVANCE_MS);
    return () => window.clearInterval(timer);
  }, [autoPlaying, reduceMotion]);

  function selectStage(index: number) {
    // First interaction (click or keyboard) hands control to the user for
    // good — the walkthrough never resumes on its own after that.
    if (autoPlaying) setAutoPlaying(false);
    interacted.current = true;
    setActive(index);
  }

  function onKeyDown(event: React.KeyboardEvent<HTMLDivElement>) {
    if (event.key !== "ArrowRight" && event.key !== "ArrowLeft" && event.key !== "Home" && event.key !== "End") return;
    event.preventDefault();
    const last = STAGES.length - 1;
    let next = active;
    if (event.key === "ArrowRight") next = active === last ? 0 : active + 1;
    if (event.key === "ArrowLeft") next = active === 0 ? last : active - 1;
    if (event.key === "Home") next = 0;
    if (event.key === "End") next = last;
    selectStage(next);
    const tabs = event.currentTarget.querySelectorAll<HTMLButtonElement>('[role="tab"]');
    tabs[next]?.focus();
  }

  const ActivePanel = PANELS[STAGES[active].id];

  return (
    <section id="validation-journey" className="scroll-mt-20 pb-14" aria-labelledby="journey-heading">
      <div className="mb-5">
        <h2 id="journey-heading" className="text-lg font-semibold tracking-tight">
          The validation journey
        </h2>
        <p className="mt-0.5 max-w-2xl text-sm text-muted-foreground">
          How one transaction moves through Platrixa — from raw language to a deterministic decision.
        </p>
      </div>

      <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
        <p className="font-mono text-[11px] text-muted-foreground">
          example: <span className="text-foreground/90">{DEMO_INPUT}</span>
        </p>
        <p className="font-mono text-[11px] text-muted-foreground">
          product walkthrough — illustrative, not a live response
        </p>
      </div>

      {/* Stage navigation — clickable tabs; auto-advances until first interaction */}
      <div
        role="tablist"
        aria-label="Validation journey stages"
        onKeyDown={onKeyDown}
        className="-mx-4 flex snap-x gap-1.5 overflow-x-auto px-4 pb-1 sm:mx-0 sm:grid sm:grid-cols-6 sm:overflow-visible sm:px-0"
      >
        {STAGES.map((stage, i) => {
          const Icon = stage.icon;
          const isActive = i === active;
          return (
            <button
              key={stage.id}
              type="button"
              role="tab"
              id={`journey-tab-${stage.id}`}
              aria-selected={isActive}
              aria-controls={`journey-panel-${stage.id}`}
              tabIndex={isActive ? 0 : -1}
              onClick={() => selectStage(i)}
              className={cn(
                "min-w-[128px] shrink-0 snap-start rounded-lg border px-3 py-2 text-left transition-colors sm:min-w-0",
                isActive
                  ? "border-accent/60 bg-accent-soft text-accent"
                  : "border-border/70 bg-card text-muted-foreground hover:border-accent/30 hover:text-foreground",
              )}
            >
              <span className="flex items-center gap-1.5">
                <Icon className="size-3.5 shrink-0" aria-hidden />
                <span className="font-mono text-[10px] tracking-wide">{String(i + 1).padStart(2, "0")}</span>
              </span>
              <span className="mt-0.5 block truncate text-[12.5px] font-medium">{stage.label}</span>
            </button>
          );
        })}
      </div>

      {/* Scroll affordance for the mobile stage rail */}
      <p className="mb-2 flex items-center gap-1.5 text-[11px] text-muted-foreground sm:hidden" aria-hidden>
        <ChevronsRight className="size-3.5" />
        swipe the stage rail to explore each step
      </p>

      {/* Active stage panel */}
      {/* No aria-live here: the panel auto-advances, and announcing every
          change would spam screen readers. Keyboard users get the change
          through focus/activation of the tabs instead. */}
      <div
        className="mt-3"
        role="tabpanel"
        id={`journey-panel-${STAGES[active].id}`}
        aria-labelledby={`journey-tab-${STAGES[active].id}`}
      >
        <AnimatePresence mode="wait" initial={false}>
          <motion.div
            key={STAGES[active].id}
            initial={reduceMotion ? false : { opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            exit={reduceMotion ? { opacity: 0 } : { opacity: 0, y: -8 }}
            transition={{ duration: reduceMotion ? 0.1 : 0.22, ease: "easeOut" }}
          >
            <ActivePanel />
          </motion.div>
        </AnimatePresence>
      </div>

      <p className="mt-3 text-[11px] text-muted-foreground">
        {reduceMotion
          ? "Auto-advance is disabled because your system prefers reduced motion — click any stage."
          : "The walkthrough advances on its own until you click a stage; then it stays where you put it."}
      </p>
    </section>
  );
}
