import type { Metadata } from "next";
import Link from "next/link";
import { Card, CardContent } from "@/components/ui/card";

export const metadata: Metadata = {
  title: "Trust & Safety — Platrixa",
  description:
    "What Platrixa is, what it is not, and exactly what a VERIFIED result does and does not mean.",
};

const NOT_LIST = [
  ["Not a financial adviser", "Platrixa does not recommend transactions, strategies, or financial products."],
  ["Not an investment adviser", "Nothing here is a solicitation, recommendation, or forecast of any security or market."],
  ["Not a tax adviser", "Classification follows the documented FYJC framework only; it is not tax advice for any jurisdiction or situation."],
  ["Not a legal adviser", "Interpretations are not legal opinions and carry no legal standing."],
  ["Not an autonomous accountant", "The runtime validates and executes deterministic accounting for inputs it can prove; it does not run a business's books or act on its own."],
  ["Not a guarantee of financial correctness", "Validation is only as complete as the implemented authorities and rules it checks against."],
];

const VERIFIED_MEANS = [
  "The input passed the deterministic validation and authority requirements implemented by Platrixa.",
  "Every claim was grounded in the supplied input under the grounding gate's implemented rules.",
  "A registered deterministic authority produced the accounting result.",
];

const VERIFIED_DOES_NOT_MEAN = [
  "Everything about this financial situation is guaranteed true.",
  "The classification is the only defensible one under a different framework.",
  "Future inputs will behave the same way.",
];

const PRINCIPLES = [
  [
    "Model output is untrusted",
    "The language model only proposes an interpretation. It never decides a status, never computes accounting, and its output is discarded whenever validation or grounding fails.",
  ],
  [
    "Grounding matters",
    "Interpretations must be tied to explicit input evidence. Inferred or missing evidence fails closed to REVIEW_REQUIRED or rejection — ambiguity is surfaced, never guessed away.",
  ],
  [
    "Unsupported inputs are rejected",
    "When no deterministic authority accepts an input, the answer is UNSUPPORTED — recorded as a boundary, not silently converted into something else.",
  ],
  [
    "Ambiguous inputs may require review",
    "REVIEW_REQUIRED is a legitimate outcome: the system says what it could not prove instead of producing a confident guess.",
  ],
  [
    "Authorities are deterministic",
    "Journal entries and formulas come from versioned, test-referenced authorities — the same input and registry state produce the same result.",
  ],
  [
    "The system can be wrong",
    "Validation is bounded by the rules and data implemented today. Bugs, gaps, and framework mismatches are possible.",
  ],
  [
    "Users must independently verify consequential decisions",
    "Platrixa is validation infrastructure, not a professional. Anything consequential deserves review by a qualified human before you rely on it.",
  ],
];

export default function TrustPage() {
  return (
    <main className="mx-auto w-full max-w-4xl flex-1 px-4 py-10 sm:px-6">
      <div className="mb-7">
        <h1 className="text-2xl font-semibold tracking-tight">Trust &amp; Safety</h1>
        <p className="mt-1.5 max-w-2xl text-sm leading-relaxed text-muted-foreground">
          What this system is, what it is not, and precisely what its results mean. This page is part
          of the product, not a footnote.
        </p>
      </div>

      <div className="space-y-5">
        <Card>
          <CardContent className="pt-6">
            <h2 className="text-base font-semibold tracking-tight">What Platrixa is</h2>
            <p className="mt-2 text-sm leading-relaxed text-muted-foreground">
              Platrixa is <strong className="text-foreground">financial semantic validation
              infrastructure</strong> — a validation assistant. It takes AI-generated interpretations
              of financial language and documents, validates them against explicit schemas and
              grounding requirements, and routes them to deterministic, registry-registered
              authorities that produce the accounting result.
            </p>
            <p className="mt-2 font-mono text-xs text-muted-foreground">
              AI understands. Platrixa validates. Deterministic authorities calculate and execute.
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardContent className="pt-6">
            <h2 className="text-base font-semibold tracking-tight">What Platrixa is not</h2>
            <dl className="mt-3 space-y-2.5">
              {NOT_LIST.map(([title, body]) => (
                <div key={title} className="rounded-lg border border-border/70 bg-muted/30 px-3.5 py-2.5">
                  <dt className="text-sm font-medium">{title}</dt>
                  <dd className="mt-0.5 text-sm text-muted-foreground">{body}</dd>
                </div>
              ))}
            </dl>
          </CardContent>
        </Card>

        <Card>
          <CardContent className="pt-6">
            <h2 className="text-base font-semibold tracking-tight">What VERIFIED means — and does not</h2>
            <div className="mt-3 grid gap-3 md:grid-cols-2">
              <div className="rounded-lg border border-status-verified/35 bg-status-verified/5 p-3.5">
                <p className="font-mono text-xs font-semibold text-status-verified">VERIFIED means</p>
                <ul className="mt-2 space-y-1.5 text-sm text-muted-foreground">
                  {VERIFIED_MEANS.map((item) => (
                    <li key={item} className="flex gap-2">
                      <span className="mt-2 size-1 shrink-0 rounded-full bg-status-verified" />
                      {item}
                    </li>
                  ))}
                </ul>
              </div>
              <div className="rounded-lg border border-border bg-muted/20 p-3.5">
                <p className="font-mono text-xs font-semibold text-muted-foreground">VERIFIED does not mean</p>
                <ul className="mt-2 space-y-1.5 text-sm text-muted-foreground">
                  {VERIFIED_DOES_NOT_MEAN.map((item) => (
                    <li key={item} className="flex gap-2">
                      <span className="mt-2 size-1 shrink-0 rounded-full bg-muted-foreground" />
                      {item}
                    </li>
                  ))}
                </ul>
              </div>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardContent className="pt-6">
            <h2 className="text-base font-semibold tracking-tight">Operating principles</h2>
            <dl className="mt-3 space-y-2.5">
              {PRINCIPLES.map(([title, body]) => (
                <div key={title} className="rounded-lg border border-border/70 px-3.5 py-2.5">
                  <dt className="text-sm font-medium">{title}</dt>
                  <dd className="mt-0.5 text-sm leading-relaxed text-muted-foreground">{body}</dd>
                </div>
              ))}
            </dl>
          </CardContent>
        </Card>

        <p className="text-sm text-muted-foreground">
          Questions about scope or safety? The developer documentation describes the exact
          six-state contract and error behavior, and the{" "}
          <Link href="/capabilities" className="text-accent hover:underline">capability registry</Link>{" "}
          shows precisely what is supported, partial, unsupported, and planned — unfiltered.
        </p>
      </div>
    </main>
  );
}
