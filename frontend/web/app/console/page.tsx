import type { Metadata } from "next";
import { ValidationConsole } from "@/components/console";

export const metadata: Metadata = {
  title: "Validation Console — Platrixa",
  description:
    "Submit financial input to the deterministic Platrixa runtime and see the interpretation, validation journey, evidence, and the reason behind the decision.",
};

export default function ConsolePage() {
  return (
    <main className="mx-auto w-full max-w-4xl flex-1 px-4 py-10 sm:px-6">
      <div className="mb-6">
        <h1 className="text-2xl font-semibold tracking-tight">Validation console</h1>
        <p className="mt-1.5 max-w-2xl text-sm leading-relaxed text-muted-foreground">
          One input, one pipeline: the model interprets, the runtime validates and grounds, and the
          deterministic authorities decide. Every result carries its evidence and its reason.
        </p>
      </div>
      <ValidationConsole />
    </main>
  );
}
