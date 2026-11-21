"use client";

import { useEffect, useState } from "react";

/**
 * One-time API-key reveal.
 *
 * The token lives in the URL fragment, is posted once, and the server
 * invalidates it on success — the key cannot be retrieved a second time.
 * The key is held in component state ONLY: no localStorage, no sessionStorage,
 * no query string, no analytics.
 */

type Revealed = {
  api_key: string;
  tenant_id: string;
  api_base_url: string | null;
  docs_url: string | null;
  monthly_limit: number | null;
  warning: string;
};

export default function RevealKeyPage() {
  const [state, setState] = useState<"working" | "ready" | "error">("working");
  const [detail, setDetail] = useState("Retrieving your key…");
  const [revealed, setRevealed] = useState<Revealed | null>(null);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    const hash = window.location.hash.startsWith("#") ? window.location.hash.slice(1) : "";
    const params = new URLSearchParams(hash);
    const token = params.get("token") || "";
    window.history.replaceState(null, "", window.location.pathname);

    if (!token) {
      setState("error");
      setDetail("This link is missing its one-time token. Use the link from your email.");
      return;
    }

    let cancelled = false;
    (async () => {
      try {
        const response = await fetch("/v1/developer/keys/reveal", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ token }),
        });
        const payload = await response.json().catch(() => null);
        if (cancelled) return;
        if (!response.ok) {
          setState("error");
          setDetail(
            payload?.error?.code === "REVEAL_LINK_EXPIRED"
              ? "This key link has expired. Ask the operator to rotate the key so a new one is issued."
              : "This key link has already been used. A key can only be retrieved once — rotate it for a replacement.",
          );
          return;
        }
        setRevealed(payload as Revealed);
        setState("ready");
      } catch {
        if (!cancelled) {
          setState("error");
          setDetail("The key could not be retrieved. Check your connection and retry the link.");
        }
      }
    })();

    return () => {
      cancelled = true;
    };
  }, []);

  async function copyKey() {
    if (!revealed) return;
    try {
      await navigator.clipboard.writeText(revealed.api_key);
      setCopied(true);
      setTimeout(() => setCopied(false), 2500);
    } catch {
      setCopied(false);
    }
  }

  return (
    <main className="mx-auto w-full max-w-3xl flex-1 px-4 py-16 sm:px-6">
      <h1 className="text-2xl font-semibold tracking-tight">Your API key</h1>

      {state === "working" ? <p className="mt-3 text-sm text-muted-foreground">{detail}</p> : null}

      {state === "error" ? (
        <>
          <p className="mt-3 text-sm leading-relaxed text-muted-foreground" role="alert">
            {detail}
          </p>
          <a href="/developer" className="mt-6 inline-block text-sm font-medium text-accent hover:underline">
            Back to the developer section →
          </a>
        </>
      ) : null}

      {state === "ready" && revealed ? (
        <div className="mt-5 space-y-4">
          <div className="rounded-lg border border-accent/40 bg-accent-soft/40 p-4">
            <p className="text-xs font-semibold uppercase tracking-wider text-accent">
              Shown once — copy it now
            </p>
            <code className="mt-2 block break-all rounded-md border border-border bg-background p-3 font-mono text-xs">
              {revealed.api_key}
            </code>
            <div className="mt-3 flex flex-wrap gap-2">
              <button
                type="button"
                onClick={copyKey}
                className="rounded-lg bg-accent px-3 py-1.5 text-sm font-medium text-accent-foreground"
              >
                {copied ? "Copied" : "Copy key"}
              </button>
              <button
                type="button"
                onClick={() => setRevealed(null)}
                className="rounded-lg border border-border px-3 py-1.5 text-sm hover:bg-muted"
              >
                I&apos;ve saved it — hide
              </button>
            </div>
          </div>

          <dl className="grid gap-2 sm:grid-cols-2">
            <div className="rounded-lg border border-border/70 bg-muted/30 px-3 py-2">
              <dt className="text-xs text-muted-foreground">Initial quota</dt>
              <dd className="text-sm">
                {revealed.monthly_limit ?? "—"} admitted requests per UTC month
              </dd>
            </div>
            <div className="rounded-lg border border-border/70 bg-muted/30 px-3 py-2">
              <dt className="text-xs text-muted-foreground">API base URL</dt>
              <dd className="break-all font-mono text-sm">{revealed.api_base_url ?? "See your onboarding email"}</dd>
            </div>
          </dl>

          <div className="rounded-lg border border-border/70 px-3 py-2.5 text-xs leading-relaxed text-muted-foreground">
            <strong className="text-foreground">Store it server-side.</strong> Put the key in an
            environment variable or secret manager on your backend. Never embed it in browser
            JavaScript, a public mobile app, a URL, or analytics.
          </div>

          <pre className="overflow-x-auto rounded-lg border border-border bg-muted/40 p-4 font-mono text-[12px] leading-relaxed">
{`curl -X POST "${revealed.api_base_url ?? "<API_BASE_URL>"}/process" \\
  -H "Content-Type: application/json" \\
  -H "X-Platrixa-API-Key: $PLATRIXA_API_KEY" \\
  -d '{"raw_input": "Purchased furniture for cash Rs. 15,000"}'`}
          </pre>
        </div>
      ) : null}
    </main>
  );
}
