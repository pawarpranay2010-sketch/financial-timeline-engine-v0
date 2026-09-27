"use client";

import { useCallback, useState } from "react";
import { ValidationWorkspace, type Phase } from "@/components/workspace";
import { ValidationProgress } from "@/components/home/validation-progress";

/**
 * Try It — the live validation workspace embedded on the homepage, with
 * the truthful validation-progress timeline bound to its real phases.
 * All processing behavior is unchanged; this is a composition wrapper.
 */
export function TryItSection() {
  const [phase, setPhase] = useState<Phase>("idle");
  const onPhaseChange = useCallback((next: Phase) => setPhase(next), []);

  return (
    <section id="try-it" className="scroll-mt-20 pb-14" aria-labelledby="try-it-heading">
      <div className="mb-5">
        <h2 id="try-it-heading" className="text-lg font-semibold tracking-tight">
          Try Platrixa
        </h2>
        <p className="mt-0.5 max-w-2xl text-sm text-muted-foreground">
          Validate financial meaning before it reaches execution. Text and documents run through the
          real deterministic runtime — nothing on this page is simulated.
        </p>
      </div>
      <ValidationWorkspace onPhaseChange={onPhaseChange} progress={<ValidationProgress phase={phase} />} />
    </section>
  );
}
