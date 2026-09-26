import type { Metadata } from "next";
import { AuthorityGroups } from "@/components/authority-groups";

export const metadata: Metadata = {
  title: "Capability Explorer — Platrixa",
  description:
    "What the deterministic runtime can prove and execute, grouped by authority from the live capability registry: Accounting Kernel, Formula Authority, and Finance Knowledge.",
};

export default function CapabilitiesPage() {
  return (
    <main className="mx-auto w-full max-w-5xl flex-1 px-4 py-10 sm:px-6">
      <div className="mb-6">
        <h1 className="text-2xl font-semibold tracking-tight">Capability explorer</h1>
        <p className="mt-1.5 max-w-2xl text-sm leading-relaxed text-muted-foreground">
          A developer evaluating Platrixa should be able to answer: what can it support, what inputs
          does it require, and what is explicitly unsupported? This page is the registry, unfiltered
          by marketing.
        </p>
      </div>
      <AuthorityGroups />
    </main>
  );
}
