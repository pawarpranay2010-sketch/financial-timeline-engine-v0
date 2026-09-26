"use client";

import { motion, useReducedMotion } from "framer-motion";
import { Check, CircleDashed, TriangleAlert, X } from "lucide-react";
import type { KernelProcessResponse } from "@/lib/types";
import { cn } from "@/lib/utils";

/**
 * Validation journey — a presentation of the backend's documented pipeline
 * (README.md), with each stage's outcome derived ONLY from fields the
 * backend actually returned. When a stage's outcome is not reported, the
 * stage renders as "Not reported" (never assumed success). Reduced motion
 * is respected.
 *
 * Stage derivation rules (backend field → stage outcome):
 *   interpretation        response.interpretation != null      → pass | not reported
 *   schema validation     status == VALIDATION_FAILED → fail;
 *                         interpretation present otherwise     → pass | not reported
 *   grounding             status in (GROUNDING_FAILED, REVIEW_REQUIRED) with
 *                         grounding_issues → warn/fail; status VERIFIED → pass;
 *                         otherwise not reported
 *   capability / authority interpretation present & status not
 *                         UNSUPPORTED_TRANSACTION/FORBIDDEN_OUTPUT → pass |
 *                         not reported; UNSUPPORTED → blocked
 *   decision              engine status rendered verbatim
 */

type StageOutcome = "pass" | "warn" | "fail" | "pending" | "unreported";

interface StageView {
  key: string;
  label: string;
  outcome: StageOutcome;
  detail?: string;
}

function deriveStages(result: KernelProcessResponse): StageView[] {
  const status = result.status;
  const hasInterpretation = result.interpretation != null;
  const hasIssues = (result.issues ?? []).length > 0;
  const hasGroundingIssues = (result.grounding_issues ?? []).length > 0;

  const interpretation: StageView = hasInterpretation
    ? { key: "interpretation", label: "AI Interpretation", outcome: "pass", detail: "candidate semantic IR returned" }
    : { key: "interpretation", label: "AI Interpretation", outcome: "unreported", detail: "Not reported" };

  let schema: StageView;
  if (status === "VALIDATION_FAILED") {
    schema = { key: "schema", label: "Schema Validation", outcome: "fail", detail: "field contract not satisfied" };
  } else if (hasInterpretation) {
    schema = { key: "schema", label: "Schema Validation", outcome: "pass", detail: "18-field contract satisfied" };
  } else {
    schema = { key: "schema", label: "Schema Validation", outcome: "unreported", detail: "Not reported" };
  }

  let grounding: StageView;
  if (status === "GROUNDING_FAILED") {
    grounding = { key: "grounding", label: "Grounding", outcome: "fail", detail: "claims not tied to input evidence" };
  } else if (status === "VERIFIED") {
    grounding = { key: "grounding", label: "Grounding", outcome: "pass", detail: "grounded in explicit input" };
  } else if (hasGroundingIssues) {
    grounding = {
      key: "grounding",
      label: "Grounding",
      outcome: "warn",
      detail: "grounding evidence recorded — see evidence section",
    };
  } else if (status === "REVIEW_REQUIRED") {
    grounding = {
      key: "grounding",
      label: "Grounding",
      outcome: "warn",
      detail: "insufficient evidence for a deterministic decision",
    };
  } else {
    grounding = { key: "grounding", label: "Grounding", outcome: "unreported", detail: "Not reported" };
  }

  let authority: StageView;
  if (status === "UNSUPPORTED_TRANSACTION" || status === "FORBIDDEN_OUTPUT") {
    authority = { key: "authority", label: "Capability / Authority", outcome: "fail", detail: "no authority accepted the input" };
  } else if (status === "BLOCKED") {
    authority = { key: "authority", label: "Capability / Authority", outcome: "fail", detail: "rejected at a safety boundary" };
  } else if (status === "VERIFIED") {
    authority = { key: "authority", label: "Capability / Authority", outcome: "pass", detail: "deterministic authority executed" };
  } else if (hasInterpretation && !hasIssues) {
    authority = { key: "authority", label: "Capability / Authority", outcome: "pass", detail: "routed by capability registry" };
  } else {
    authority = { key: "authority", label: "Capability / Authority", outcome: "unreported", detail: "Not reported" };
  }

  const decision: StageView = {
    key: "decision",
    label: "Decision",
    outcome: status === "VERIFIED" ? "pass" : status === "MODEL_UNAVAILABLE" ? "pending" : hasInterpretation ? "warn" : "fail",
    detail: result.status,
  };

  return [interpretation, schema, grounding, authority, decision];
}

const OUTCOME_ICON: Record<StageOutcome, React.ComponentType<{ className?: string }>> = {
  pass: Check,
  warn: TriangleAlert,
  fail: X,
  pending: CircleDashed,
  unreported: MinusIcon,
};

function MinusIcon(props: { className?: string }) {
  return <span className={cn("inline-block h-[2px] w-2.5 rounded bg-current", props.className)} aria-hidden />;
}

const OUTCOME_STYLE: Record<StageOutcome, string> = {
  pass: "border-status-verified/40 bg-status-verified/10 text-status-verified",
  warn: "border-status-review/40 bg-status-review/10 text-status-review",
  fail: "border-status-unsupported/40 bg-status-unsupported/10 text-status-unsupported",
  pending: "border-status-processing/40 bg-status-processing/10 text-status-processing",
  unreported: "border-border bg-muted/40 text-muted-foreground",
};

export function ValidationJourney({ result }: { result: KernelProcessResponse }) {
  const stages = deriveStages(result);
  const reduceMotion = useReducedMotion();

  return (
    <ol className="relative flex snap-x gap-1.5 overflow-x-auto pb-1 sm:grid sm:grid-cols-5 sm:gap-1 sm:overflow-visible">
      {stages.map((stage, i) => {
        const Icon = OUTCOME_ICON[stage.outcome];
        const isLast = i === stages.length - 1;
        return (
          <li key={stage.key} className="flex min-w-[46%] snap-start items-stretch sm:min-w-0">
            <motion.div
              initial={reduceMotion ? false : { opacity: 0, y: 6 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: reduceMotion ? 0 : i * 0.07, duration: 0.28 }}
              className={cn(
                "flex-1 rounded-lg border p-3",
                OUTCOME_STYLE[stage.outcome],
              )}
            >
              <div className="flex items-center gap-2">
                <Icon className="size-3.5 shrink-0" aria-hidden />
                <p className="truncate text-xs font-medium">{stage.label}</p>
              </div>
              <p className="mt-1.5 truncate font-mono text-[10.5px] leading-snug opacity-90" title={stage.detail}>
                {stage.detail}
              </p>
            </motion.div>
            {!isLast && (
              <span
                aria-hidden
                className="mx-0.5 mt-4 hidden h-px w-2 shrink-0 bg-border sm:block"
              />
            )}
          </li>
        );
      })}
    </ol>
  );
}
