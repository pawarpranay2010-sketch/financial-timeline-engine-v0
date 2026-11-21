"use client";

import { useEffect, useState } from "react";

/**
 * Verification landing page.
 *
 * The token arrives in the URL FRAGMENT (`#token=…`), which is never sent to
 * a server, logged, or used as a Referer. It is read once, posted to the
 * backend, and then removed from the address bar.
 *
 * Expired or already-used links are recoverable: the resend form asks the API
 * for a fresh single-use link. The response is deliberately neutral (it never
 * reveals whether an application exists for the address).
 */

type State = "working" | "verified" | "review" | "error";

export default function VerifyEmailPage() {
  const [state, setState] = useState<State>("working");
  const [detail, setDetail] = useState<string>("Verifying your email…");
  const [expired, setExpired] = useState(false);

  useEffect(() => {
    const hash = window.location.hash.startsWith("#") ? window.location.hash.slice(1) : "";
    const params = new URLSearchParams(hash);
    const token = params.get("token") || "";
    window.history.replaceState(null, "", window.location.pathname);

    if (!token) {
      setExpired(false);
      setState("error");
      setDetail("This link is missing its verification token. Use the link from your email, or request a new one below.");
      return;
    }

    let cancelled = false;
    (async () => {
      try {
        const response = await fetch("/v1/developer/verify-email", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ token }),
        });
        const payload = await response.json().catch(() => null);
        if (cancelled) return;
        if (!response.ok) {
          const isExpired = payload?.error?.code === "VERIFICATION_TOKEN_EXPIRED";
          setExpired(isExpired);
          setState("error");
          setDetail(
            isExpired
              ? "This verification link has expired. Request a new one below."
              : "This verification link is invalid or has already been used. Request a new one below.",
          );
          return;
        }
        if (payload?.status === "REVIEW_REQUIRED") {
          setState("review");
          setDetail(
            "Your email is verified. This application needs a quick operator review before access is issued — you will be notified by email. There is nothing else to do right now.",
          );
          return;
        }
        setState("verified");
        setDetail(
          payload?.status === "ACTIVE"
            ? "Verified. Your account is provisioned — check your inbox for the one-time key link."
            : "Email verified. Eligibility and provisioning are running now; watch your inbox for the one-time key link.",
        );
      } catch {
        if (!cancelled) {
          setState("error");
          setDetail("The verification request could not be sent. Check your connection and retry the link.");
        }
      }
    })();

    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <main className="mx-auto w-full max-w-2xl flex-1 px-4 py-16 sm:px-6">
      <h1 className="text-2xl font-semibold tracking-tight">Email verification</h1>
      <p className="mt-3 text-sm leading-relaxed text-muted-foreground" role="status" aria-live="polite">
        {detail}
      </p>

      {state === "verified" || state === "review" ? (
        <div className="mt-6 rounded-lg border border-border/70 bg-muted/30 px-4 py-3 text-sm text-muted-foreground">
          Your API key is delivered once, through a short-lived link, and is never shown
          again. Store it in a server-side secret manager — never in browser JavaScript.
        </div>
      ) : null}

      {state === "error" ? (
        <>
          {expired ? (
            <p className="mt-3 text-xs text-muted-foreground">
              Verification links are single-use and time-limited. A new link replaces the old
              one, so use the most recent email.
            </p>
          ) : null}
          <ResendForm />
          <a href="/developer" className="mt-6 inline-block text-sm font-medium text-accent hover:underline">
            Back to the developer section →
          </a>
        </>
      ) : null}
    </main>
  );
}

function ResendForm() {
  const [email, setEmail] = useState("");
  const [state, setState] = useState<"idle" | "sending" | "sent" | "error">("idle");
  const [message, setMessage] = useState("");

  async function onSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!email.trim()) {
      setState("error");
      setMessage("Enter the work email you applied with.");
      return;
    }
    setState("sending");
    setMessage("");
    try {
      const response = await fetch("/v1/developer/resend-verification", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ email: email.trim() }),
      });
      const payload = await response.json().catch(() => null);
      if (response.ok) {
        setState("sent");
        setMessage(
          "If an application is awaiting verification, a new single-use link is on its way. Check spam before requesting another.",
        );
        return;
      }
      if (response.status === 429) {
        setState("error");
        setMessage("A link was sent recently. Wait a minute, then try again.");
        return;
      }
      setState("error");
      setMessage(payload?.error?.message || "The link could not be resent. Please try again.");
    } catch {
      setState("error");
      setMessage("The request could not be sent. Check your connection and try again.");
    }
  }

  return (
    <form className="mt-6 space-y-2" onSubmit={onSubmit} noValidate>
      <label htmlFor="resend-email" className="block text-sm font-medium">
        Request a new verification link
      </label>
      <div className="flex flex-col gap-2 sm:flex-row">
        <input
          id="resend-email"
          name="email"
          type="email"
          inputMode="email"
          autoComplete="email"
          maxLength={320}
          value={email}
          onChange={(event) => {
            setEmail(event.target.value);
            if (state === "error") setState("idle");
          }}
          aria-invalid={state === "error"}
          aria-describedby={message ? "resend-message" : undefined}
          placeholder="you@company.com"
          className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm outline-none transition-colors focus:border-accent/60 focus:ring-2 focus:ring-accent/15 sm:flex-1"
        />
        <button
          type="submit"
          disabled={state === "sending"}
          className="rounded-lg bg-accent px-4 py-2 text-sm font-medium text-accent-foreground transition-opacity hover:opacity-90 disabled:opacity-60"
        >
          {state === "sending" ? "Sending…" : "Resend link"}
        </button>
      </div>
      {message ? (
        <p
          id="resend-message"
          role="status"
          aria-live="polite"
          className={state === "error" ? "text-xs text-destructive" : "text-xs text-muted-foreground"}
        >
          {message}
        </p>
      ) : null}
    </form>
  );
}
