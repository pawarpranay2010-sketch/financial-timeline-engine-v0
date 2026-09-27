"use client";

import { HeroPipeline } from "@/components/home/hero-pipeline";
import { TryItSection } from "@/components/home/try-it-section";
import { ValidationJourneySection } from "@/components/home/validation-journey-section";
import { WhyPlatrixa } from "@/components/home/why-platrixa";
import { FailureModes } from "@/components/home/failure-modes";
import { AuthoritiesSection } from "@/components/home/authorities-section";
import { DeveloperFlow } from "@/components/home/developer-flow";
import { TrustSafetySection } from "@/components/home/trust-safety-section";
import { RegistryStrip } from "@/components/home/registry-strip";

/**
 * Homepage — one continuous product story:
 *
 *   HERO → TRY IT → VALIDATION JOURNEY → RESULT/EVIDENCE (inside the
 *   journey) → WHY PLATRIXA → FAILURE MODES → AUTHORITIES → DEVELOPER
 *   EXPERIENCE → TRUST & SAFETY → (site footer)
 *
 * The validation story is the hero; animation only communicates the
 * architecture. The live registry supplies every number shown.
 */
export default function LandingPage() {
  return (
    <main className="flex-1">
      <div className="mx-auto w-full max-w-6xl px-4 sm:px-6">
        <HeroPipeline />
        <TryItSection />
        <ValidationJourneySection />
        <RegistryStrip />
        <WhyPlatrixa />
        <FailureModes />
        <AuthoritiesSection />
        <DeveloperFlow />
        <TrustSafetySection />
      </div>
    </main>
  );
}
