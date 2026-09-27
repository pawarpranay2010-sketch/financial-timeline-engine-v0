import type { Metadata } from "next";
import { DeveloperSection } from "@/components/developer-section";

export const metadata: Metadata = {
  title: "Developer API — Platrixa",
  description:
    "The versioned /v1 developer contract over the deterministic Platrixa runtime: endpoints, six-state API status, authentication, and error behavior.",
};

export default function DeveloperPage() {
  return (
    <main className="mx-auto w-full max-w-4xl flex-1 px-4 py-10 sm:px-6">
      <div className="mb-6">
        <h1 className="text-2xl font-semibold tracking-tight">Developer API</h1>
        <p className="mt-1.5 max-w-2xl text-sm leading-relaxed text-muted-foreground">
          The deterministic runtime behind this UI is exposed as a versioned, machine-readable API.
          The engine&apos;s terminal state is the only status authority — this UI (and your
          integration) should act on it, never reinterpret it.
        </p>
      </div>
      <DeveloperSection />
    </main>
  );
}
