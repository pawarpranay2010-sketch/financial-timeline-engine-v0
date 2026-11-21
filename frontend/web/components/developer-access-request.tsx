"use client";

import { useState } from "react";
import { Card, CardContent } from "@/components/ui/card";
import { cn } from "@/lib/utils";

/**
 * Public "Request API Access" form.
 *
 * Six fields, validated here for fast feedback and AGAIN on the server (the
 * server is the only authority — this component never decides eligibility).
 * Submits to the same-origin `/v1` namespace, which is forwarded by the
 * existing proxy (visitor assertion + CSRF Origin check applied there). No
 * credentials, tenant ids, quotas or approval states are sent.
 */

const USAGE_OPTIONS = [
  { value: "hobby", label: "Hobby / prototype" },
  { value: "small", label: "Small — a few hundred calls a month" },
  { value: "medium", label: "Medium — a few thousand calls a month" },
  { value: "large", label: "Large — production volume" },
] as const;

type FormState = {
  name: string;
  email: string;
  company: string;
  building: string;
  expected_usage: string;
  profile_url: string;
};

const EMPTY: FormState = {
  name: "",
  email: "",
  company: "",
  building: "",
  expected_usage: "small",
  profile_url: "",
};

const LIMITS = {
  name: 120,
  email: 320,
  company: 160,
  building: 1000,
  profile_url: 300,
} as const;

const LABELS: Record<string, string> = {
  name: "Name",
  email: "Work email",
  company: "Company or project name",
  building: "What you're building",
  expected_usage: "Expected API usage",
  profile_url: "Website or GitHub profile",
};

function Field({
  id,
  label,
  required,
  hint,
  error,
  children,
}: {
  id: string;
  label: string;
  required?: boolean;
  hint?: string;
  error?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="space-y-1.5">
      <label htmlFor={id} className="block text-sm font-medium">
        {label}
        {required ? <span className="ml-1 text-accent">*</span> : <span className="ml-1 text-xs text-muted-foreground">optional</span>}
      </label>
      {children}
      {hint && !error ? <p className="text-xs text-muted-foreground">{hint}</p> : null}
      {error ? (
        <p id={`${id}-error`} className="text-xs text-destructive" role="alert">
          {error}
        </p>
      ) : null}
    </div>
  );
}

const inputClass =
  "w-full rounded-lg border border-border bg-background px-3 py-2 text-sm outline-none transition-colors focus:border-accent/60 focus:ring-2 focus:ring-accent/15";

export function DeveloperAccessRequest() {
  const [form, setForm] = useState<FormState>(EMPTY);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [status, setStatus] = useState<"idle" | "submitting" | "sent" | "failed">("idle");
  const [message, setMessage] = useState<string>("");

  const update = (key: keyof FormState, value: string) => {
    setForm((prev) => ({ ...prev, [key]: value }));
    setErrors((prev) => ({ ...prev, [key]: "" }));
  };

  function validate(): boolean {
    const next: Record<string, string> = {};
    if (!form.name.trim()) next.name = "Name is required.";
    if (!form.email.trim()) next.email = "Work email is required.";
    else if (!/^[^\s@]+@[^\s@]+\.[A-Za-z]{2,}$/.test(form.email.trim()))
      next.email = "Enter a valid email address.";
    if (!form.company.trim()) next.company = "Company or project name is required.";
    if (!form.building.trim()) next.building = "Tell us what you're building.";
    if (!form.expected_usage) next.expected_usage = "Choose an expected usage level.";
    if (form.profile_url.trim()) {
      try {
        const url = new URL(form.profile_url.trim());
        if (!["http:", "https:"].includes(url.protocol) || !url.hostname.includes("."))
          throw new Error("bad url");
      } catch {
        next.profile_url = "Enter a valid http(s) URL.";
      }
    }
    setErrors(next);
    return Object.keys(next).length === 0;
  }

  async function onSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!validate()) return;
    setStatus("submitting");
    setMessage("");
    try {
      const response = await fetch("/v1/developer/access-requests", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ ...form, profile_url: form.profile_url.trim() || null }),
      });
      const payload = await response.json().catch(() => null);
      if (response.status === 202) {
        setStatus("sent");
        setForm(EMPTY);
        return;
      }
      const error = payload?.error ?? {};
      if (error.field && typeof error.field === "string") {
        setErrors({ [error.field]: error.message || "Please check this field." });
      }
      setMessage(
        error.code === "RATE_LIMITED" || error.code === "ONBOARDING_CAPACITY_REACHED"
          ? "We're receiving a lot of requests right now. Please try again later."
          : error.message || "The request could not be submitted. Please try again.",
      );
      setStatus("failed");
    } catch {
      setMessage("The request could not be sent. Check your connection and try again.");
      setStatus("failed");
    }
  }

  if (status === "sent") {
    return (
      <Card>
        <CardContent className="pt-6">
          <h2 className="text-sm font-semibold">Application received</h2>
          <p className="mt-2 text-sm leading-relaxed text-muted-foreground">
            Check the inbox for the work email you entered. Access is issued only after
            that address is verified — the verification link is single-use and expires.
          </p>
          <p className="mt-2 text-sm leading-relaxed text-muted-foreground">
            Once verified, eligible applications are provisioned automatically: you receive
            an initial monthly quota and a one-time link to retrieve your API key.
            Suspicious or ambiguous requests are reviewed by the operator instead.
          </p>
          <p className="mt-3 rounded-md border border-border/70 bg-muted/30 px-3 py-2 text-xs text-muted-foreground">
            Didn&apos;t arrive? Check spam, then use the resend option in the verification
            email. Never share your API key, and only ever use it from a server.
          </p>
          <button
            type="button"
            onClick={() => setStatus("idle")}
            className="mt-4 rounded-lg border border-border px-3 py-1.5 text-sm transition-colors hover:bg-muted"
          >
            Submit another application
          </button>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardContent className="pt-6">
        <h2 className="text-sm font-semibold">Request API access</h2>
        <p className="mt-1.5 text-sm leading-relaxed text-muted-foreground">
          A work email is required because access is tied to a verified address. Applications
          are reviewed by the runtime&apos;s own eligibility rules — there is no way to skip
          verification or self-approve.
        </p>
        <form
          className="mt-5 space-y-4"
          onSubmit={onSubmit}
          noValidate
          aria-busy={status === "submitting"}
        >
          <div className="grid gap-4 sm:grid-cols-2">
            <Field id="name" label="Name" required error={errors.name}>
              <input
                id="name"
                name="name"
                autoComplete="name"
                maxLength={LIMITS.name}
                aria-invalid={Boolean(errors.name)}
                aria-describedby={errors.name ? "name-error" : undefined}
                className={inputClass}
                value={form.name}
                onChange={(event) => update("name", event.target.value)}
              />
            </Field>
            <Field id="email" label="Work email" required error={errors.email}>
              <input
                id="email"
                name="email"
                type="email"
                inputMode="email"
                autoComplete="email"
                maxLength={LIMITS.email}
                aria-invalid={Boolean(errors.email)}
                aria-describedby={errors.email ? "email-error" : undefined}
                className={inputClass}
                value={form.email}
                onChange={(event) => update("email", event.target.value)}
              />
            </Field>
          </div>

          <Field id="company" label="Company or project name" required error={errors.company}>
            <input
              id="company"
              name="company"
              autoComplete="organization"
              maxLength={LIMITS.company}
              className={inputClass}
              value={form.company}
              onChange={(event) => update("company", event.target.value)}
            />
          </Field>

          <Field
            id="building"
            label="What you're building"
            required
            hint="A sentence or two is enough — the integration, the ledger flow, the product."
            error={errors.building}
          >
            <textarea
              id="building"
              name="building"
              rows={4}
              maxLength={LIMITS.building}
              aria-invalid={Boolean(errors.building)}
              aria-describedby={errors.building ? "building-error" : undefined}
              className={cn(inputClass, "resize-y")}
              value={form.building}
              onChange={(event) => update("building", event.target.value)}
            />
          </Field>

          <Field id="expected_usage" label="Expected API usage" required error={errors.expected_usage}>
            <select
              id="expected_usage"
              name="expected_usage"
              className={inputClass}
              value={form.expected_usage}
              onChange={(event) => update("expected_usage", event.target.value)}
            >
              {USAGE_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </Field>

          <Field
            id="profile_url"
            label="Website or GitHub profile"
            hint="Helps the operator verify the project. Must be http(s)."
            error={errors.profile_url}
          >
            <input
              id="profile_url"
              name="profile_url"
              type="url"
              inputMode="url"
              maxLength={LIMITS.profile_url}
              placeholder="https://github.com/your-org"
              className={inputClass}
              value={form.profile_url}
              onChange={(event) => update("profile_url", event.target.value)}
            />
          </Field>

          {status === "failed" && message ? (
            <p className="rounded-md border border-destructive/40 bg-destructive/5 px-3 py-2 text-sm text-destructive" role="alert">
              {message}
            </p>
          ) : null}

          <div className="flex flex-wrap items-center gap-3">
            <button
              type="submit"
              disabled={status === "submitting"}
              className="rounded-lg bg-accent px-4 py-2 text-sm font-medium text-accent-foreground transition-opacity hover:opacity-90 disabled:opacity-60"
            >
              {status === "submitting" ? "Submitting…" : "Request access"}
            </button>
            <p className="text-xs text-muted-foreground">
              We email a single-use verification link. No key is issued before verification.
            </p>
          </div>
        </form>
      </CardContent>
    </Card>
  );
}

export function RequestAccessSection() {
  return (
    <div className="space-y-5">
      <DeveloperAccessRequest />
      <Card>
        <CardContent className="pt-6">
          <h2 className="text-sm font-semibold">What happens next</h2>
          <ol className="mt-3 space-y-2 text-sm text-muted-foreground">
            {[
              "Verify your work email with the single-use link (expires, and only works once).",
              "The backend checks eligibility: work-email domain, application quality, duplicate tenants, onboarding limits.",
              "Eligible applications get a dedicated tenant, one API key and an initial monthly quota automatically.",
              "You receive a one-time link to retrieve the key. After it is shown, it cannot be retrieved again.",
              "Ambiguous requests go to REVIEW_REQUIRED for the operator — no key is issued while they are in review.",
            ].map((item, index) => (
              <li key={index} className="flex gap-2.5">
                <span className="mt-0.5 flex size-5 shrink-0 items-center justify-center rounded-full border border-border font-mono text-[10px] text-muted-foreground">
                  {index + 1}
                </span>
                {item}
              </li>
            ))}
          </ol>
        </CardContent>
      </Card>
    </div>
  );
}
