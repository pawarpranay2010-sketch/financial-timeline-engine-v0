import { Card, CardContent } from "@/components/ui/card";
import { cn } from "@/lib/utils";

/**
 * Pipeline visualization — Input → Interpretation → Schema/Grounding →
 * Authority → Result. Presentation only: the stages describe the backend's
 * documented flow (README.md); the frontend executes none of it.
 */

const STAGES = [
  { key: "input", label: "Input", hint: "your words, verbatim" },
  { key: "interpretation", label: "Interpretation", hint: "candidate semantic IR" },
  { key: "validation", label: "Validation", hint: "schema + grounding" },
  { key: "authority", label: "Authority", hint: "deterministic execution" },
  { key: "result", label: "Result", hint: "status + evidence" },
] as const;

export type PipelineStageKey = (typeof STAGES)[number]["key"];

export function Pipeline({
  active,
  className,
}: {
  /** Stages known to be complete; `result` activates when a status exists. */
  active?: ReadonlyArray<PipelineStageKey>;
  className?: string;
}) {
  const done = new Set(active ?? []);
  return (
    <Card className={cn("border-border/70 bg-card/60 backdrop-blur-sm", className)}>
      <CardContent className="px-5 py-4">
        <ol className="flex flex-wrap items-stretch gap-x-1 gap-y-2 text-xs">
          {STAGES.map((stage, i) => {
            const isActive = done.has(stage.key);
            return (
              <li key={stage.key} className="flex min-w-0 flex-1 items-center gap-1">
                <div
                  className={cn(
                    "min-w-0 flex-1 rounded-md border px-3 py-2 transition-colors duration-300",
                    isActive
                      ? "border-accent/40 bg-accent-soft"
                      : "border-border bg-muted/40",
                  )}
                >
                  <div className="flex items-center gap-2">
                    <span
                      className={cn(
                        "grid size-4 shrink-0 place-items-center rounded-full border font-mono text-[10px]",
                        isActive
                          ? "border-accent/60 bg-accent text-accent-foreground"
                          : "border-border text-muted-foreground",
                      )}
                      aria-hidden
                    >
                      {i + 1}
                    </span>
                    <span
                      className={cn(
                        "truncate font-medium",
                        isActive ? "text-foreground" : "text-muted-foreground",
                      )}
                    >
                      {stage.label}
                    </span>
                  </div>
                  <p className="mt-0.5 pl-6 truncate text-[11px] text-muted-foreground">
                    {stage.hint}
                  </p>
                </div>
                {i < STAGES.length - 1 && (
                  <span
                    className={cn(
                      "hidden h-px w-3 shrink-0 sm:block",
                      isActive ? "bg-accent/50" : "bg-border",
                    )}
                    aria-hidden
                  />
                )}
              </li>
            );
          })}
        </ol>
      </CardContent>
    </Card>
  );
}
