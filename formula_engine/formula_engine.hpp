// ============================================================================
// FT-E Sprint 7 — C++ Deterministic Formula Engine
//
// A centralized, deterministic financial calculation layer implemented in
// C++. The LLM/AI provider NEVER performs arithmetic. Verified facts produced
// by the existing Python pipeline are consumed here; this engine NEVER
// searches the web, NEVER retrieves evidence and NEVER decides whether an
// external source is trustworthy (that decision stays in Sprint 6.5).
//
// Status semantics (preserving the existing FT-E classification):
//   REPORTED_VERIFIED  — directly reported and verified in the source
//   DERIVED_VERIFIED   — calculated entirely from verified inputs
//   EXTERNAL_DERIVED   — one or more verified inputs came through
//                        Sprint 6.5 Tier 3 (REGULATORY_API / APPENDIX)
//   BLOCKED            — required inputs unavailable / invalid / conflicting
//   UNANALYZED         — no supported deterministic formula exists
//
// Arithmetic uses `long double` internally; a consistent display-rounding
// policy is applied ONLY at the output layer. Full precision is preserved
// in `value`. No AI-generated formulas are ever executed — the Formula
// Registry contains only explicitly approved formulas.
// ============================================================================
#ifndef FTE_FORMULA_ENGINE_HPP
#define FTE_FORMULA_ENGINE_HPP

#include <map>
#include <string>
#include <vector>

namespace fte {

// ---------------------------------------------------------------------------
// Input contract: one already-verified fact from the Python pipeline.
// Every field is optional metadata except `metric` (and the value itself,
// signalled by has_value). Missing metadata is preserved as-is; nothing is
// fabricated.
// ---------------------------------------------------------------------------
struct Fact {
    std::string metric;
    long double value = 0.0L;
    bool has_value = false;
    std::string unit;              // currency / unit (e.g. USD)
    std::string scale;             // units / thousands / millions / billions / crores
    std::string reporting_period;  // e.g. FY2025
    std::string provenance_tier;   // DOCUMENT / APPENDIX / REGULATORY_API / ...
    std::string document_name;
    std::string page;
    std::string evidence;
    std::string provider;
    std::string source_ref;
};

// ---------------------------------------------------------------------------
// Output contract: structured calculation result.
// ---------------------------------------------------------------------------
enum class Status {
    REPORTED_VERIFIED,
    DERIVED_VERIFIED,
    EXTERNAL_DERIVED,
    BLOCKED,
    UNANALYZED,
};

std::string status_name(Status s);

struct Result {
    Status status = Status::UNANALYZED;
    long double value = 0.0L;      // full deterministic precision
    bool has_value = false;
    std::string display_value;     // display-rounded, e.g. "36.61%"
    std::vector<std::string> calculation_steps;
    std::string lineage;           // auditable tree
    std::string block_reason;      // populated when BLOCKED
};

// ---------------------------------------------------------------------------
// Formula Registry — one immutable definition per approved formula.
// ---------------------------------------------------------------------------
struct FormulaDef {
    std::string key;
    std::string display_name;
    std::vector<std::string> required_inputs;
    std::string formula;           // human-readable representation
    std::string kind;              // "percent" | "ratio"
    int precision;                 // display rounding digits
    std::string period_mode;       // "same" | "different" | "span"
    std::vector<std::string> denominator_inputs;
};

// The approved formulas ONLY (ROE, ROA, Profit Margin, Operating Margin,
// Current Ratio, Debt to Equity, Revenue Growth, EPS Growth, CAGR).
const std::vector<FormulaDef>& registry();

// Look up a formula by canonical key; nullptr when unknown.
const FormulaDef* registry_lookup(const std::string& metric_key);

// ---------------------------------------------------------------------------
// Core entry point: validate + calculate one metric from verified facts.
// `facts` maps metric key -> verified fact. If a required input is absent
// the result is BLOCKED (the Python bridge resolves missing inputs through
// Sprint 6.5 BEFORE calling this engine — this is defense in depth).
// ---------------------------------------------------------------------------
Result calculate_metric(const std::string& metric_key,
                        const std::map<std::string, Fact>& facts);

// ---------------------------------------------------------------------------
// CLI helpers (JSON stdin -> JSON stdout).
// ---------------------------------------------------------------------------
std::string run_cli(const std::string& stdin_json);

}  // namespace fte

#endif  // FTE_FORMULA_ENGINE_HPP
