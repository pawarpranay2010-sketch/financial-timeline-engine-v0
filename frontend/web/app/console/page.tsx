import type { Metadata } from "next";
import Link from "next/link";

export const metadata: Metadata = {
  title: "Validation workspace has moved — Platrixa",
  description: "The validation console now lives at /validate.",
  robots: { index: false },
};

/** Legacy path — static-safe redirect to the validation workspace (/validate). */
export default function ConsolePage() {
  return (
    <>
      <meta httpEquiv="refresh" content="0; url=/validate" />
      <main className="mx-auto w-full max-w-4xl flex-1 px-4 py-10 sm:px-6">
        <p className="text-sm text-muted-foreground">
          The validation console has moved.{" "}
          <Link href="/validate" className="font-medium text-accent hover:underline">
            Open the validation workspace →
          </Link>
        </p>
      </main>
    </>
  );
}
