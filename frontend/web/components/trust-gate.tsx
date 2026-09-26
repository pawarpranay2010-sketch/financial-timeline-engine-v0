"use client";

import { useSyncExternalStore } from "react";
import Link from "next/link";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";

/**
 * First-visit trust panel — shown once before the first meaningful
 * validation run. Honest scope statement, no legalese. Dismissal is
 * persisted locally; "View Trust & Safety" is always available in the
 * footer and the developer page regardless.
 */

const STORAGE_KEY = "platrixa.trust-gate.acknowledged.v1";

// Tiny external store over localStorage (canonical useSyncExternalStore
// shape — no setState-in-effect, hydration-safe via the server snapshot).
let acknowledgedCache: boolean | null = null;
const listeners = new Set<() => void>();

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function getSnapshot(): boolean {
  if (acknowledgedCache === null) {
    try {
      acknowledgedCache = Boolean(window.localStorage.getItem(STORAGE_KEY));
    } catch {
      acknowledgedCache = true; // storage unavailable — footer link suffices
    }
  }
  return acknowledgedCache;
}

function getServerSnapshot(): boolean {
  return true; // server renders nothing; client reveals after hydration
}

export function TrustGate() {
  const visible = useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);

  function acknowledge() {
    acknowledgedCache = true;
    try {
      window.localStorage.setItem(STORAGE_KEY, "1");
    } catch {
      // ignore — dismissal just won't persist
    }
    listeners.forEach((l) => l());
  }

  if (!visible) return null;

  return (
    <Card className="border-accent/30 bg-accent-soft/40">
      <CardContent className="pt-6">
        <h2 className="text-base font-semibold tracking-tight">Before you use Platrixa</h2>
        <ul className="mt-3 space-y-1.5 text-sm leading-relaxed text-muted-foreground">
          <li>
            Platrixa is a <strong className="text-foreground">financial semantic validation
            assistant</strong> — it validates AI interpretations of financial language against
            deterministic authorities.
          </li>
          <li>It is not financial, tax, legal, or investment advice.</li>
          <li>
            <span className="font-mono text-xs text-status-verified">VERIFIED</span> means the input
            passed the implemented validation and authority requirements — not that everything about
            your financial situation is guaranteed true.
          </li>
          <li>Unsupported inputs are rejected; ambiguous inputs are flagged for review, never guessed.</li>
        </ul>
        <div className="mt-4 flex flex-wrap gap-2">
          <Button size="sm" onClick={acknowledge}>
            I understand
          </Button>
          <Button size="sm" variant="outline" asChild>
            <Link href="/trust">View Trust &amp; Safety</Link>
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
