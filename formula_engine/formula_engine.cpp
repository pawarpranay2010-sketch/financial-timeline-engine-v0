// ============================================================================
// FT-E Sprint 7 — C++ Deterministic Formula Engine (implementation)
// Sprint 15G: added persistent `--worker` mode (see main).
//
// Deterministic arithmetic ONLY. The engine consumes already-verified facts
// from the Python pipeline and returns structured results (value, display
// value, calculation steps, lineage, block reason). It never searches the
// web and never decides source trustworthiness — that is Sprint 6.5's job.
//
// Interface: a CLI reading one JSON document on stdin and writing one JSON
// document on stdout. Also supports `--registry` (legacy formula metadata),
// `--registry-ext` (Sprint 12A extended formula metadata),
// `--registry-fyjc` (Sprint 15D FYJC commercial arithmetic), `--selftest`
// (built-in assertions; exit code 0 on success) and `--version`.
//
// Sprint 12A additions (backward compatible):
//   * extended_registry() — additive declarative (op-driven) formulas
//     (Profit, Loss, Gross Profit, Working Capital, Asset Turnover,
//     Equity Multiplier, Profit Margin). The legacy 9 are untouched and
//     `--registry` keeps returning exactly 9 entries.
//   * solve_metric() — reverse solving for op-driven formulas using the
//     algebraic inverse, only where a unique solution exists.
//   * run_cli accepts an optional "solve_for" field.
// ============================================================================
#include "formula_engine.hpp"

#include <algorithm>
#include <cctype>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <iomanip>
#include <iostream>
#include <map>
#include <set>
#include <sstream>
#include <string>
#include <vector>

namespace fte {

// ---------------------------------------------------------------------------
// Minimal JSON value + parser/serializer (stdlib only, no external deps).
// Supports exactly what the interface needs: objects, arrays, strings,
// numbers, booleans, null.
// ---------------------------------------------------------------------------
namespace json {

struct Value {
    enum Type { NUL, BOOL, NUM, STR, ARR, OBJ } type = NUL;
    bool b = false;
    long double num = 0.0L;
    std::string str;
    std::vector<Value> arr;
    std::map<std::string, Value> obj;

    static Value null() { return Value(); }
    static Value boolean(bool v) { Value x; x.type = BOOL; x.b = v; return x; }
    static Value number(long double v) { Value x; x.type = NUM; x.num = v; return x; }
    static Value string(std::string v) { Value x; x.type = STR; x.str = std::move(v); return x; }
    static Value array() { Value x; x.type = ARR; return x; }
    static Value object() { Value x; x.type = OBJ; return x; }

    const Value* get(const std::string& key) const {
        auto it = obj.find(key);
        return it == obj.end() ? nullptr : &it->second;
    }
};

static inline Value null() { return Value::null(); }
static inline Value boolean(bool v) { return Value::boolean(v); }
static inline Value number(long double v) { return Value::number(v); }
static inline Value string(const std::string& v) { return Value::string(v); }
static inline Value array() { return Value::array(); }
static inline Value object() { return Value::object(); }

static void skip_ws(const std::string& s, size_t& i) {
    while (i < s.size() && std::isspace(static_cast<unsigned char>(s[i]))) ++i;
}

static std::string parse_string(const std::string& s, size_t& i) {
    // i points at the opening quote.
    ++i;
    std::string out;
    while (i < s.size() && s[i] != '"') {
        char c = s[i];
        if (c == '\\' && i + 1 < s.size()) {
            char e = s[i + 1];
            switch (e) {
                case '"': out += '"'; i += 2; break;
                case '\\': out += '\\'; i += 2; break;
                case '/': out += '/'; i += 2; break;
                case 'n': out += '\n'; i += 2; break;
                case 't': out += '\t'; i += 2; break;
                case 'r': out += '\r'; i += 2; break;
                case 'b': out += '\b'; i += 2; break;
                case 'f': out += '\f'; i += 2; break;
                case 'u': {
                    // \uXXXX — decode into UTF-8 (only BMP needed here).
                    if (i + 5 < s.size()) {
                        unsigned int cp = 0;
                        for (int k = 1; k <= 4; ++k) {
                            char h = s[i + 1 + k];
                            cp <<= 4;
                            if (h >= '0' && h <= '9') cp |= (h - '0');
                            else if (h >= 'a' && h <= 'f') cp |= (h - 'a' + 10);
                            else if (h >= 'A' && h <= 'F') cp |= (h - 'A' + 10);
                        }
                        if (cp < 0x80) out += static_cast<char>(cp);
                        else if (cp < 0x800) {
                            out += static_cast<char>(0xC0 | (cp >> 6));
                            out += static_cast<char>(0x80 | (cp & 0x3F));
                        } else {
                            out += static_cast<char>(0xE0 | (cp >> 12));
                            out += static_cast<char>(0x80 | ((cp >> 6) & 0x3F));
                            out += static_cast<char>(0x80 | (cp & 0x3F));
                        }
                        i += 6;
                    } else {
                        i = s.size();
                    }
                    break;
                }
                default: out += e; i += 2; break;
            }
        } else {
            out += c;
            ++i;
        }
    }
    if (i < s.size()) ++i;  // closing quote
    return out;
}

static Value parse_value(const std::string& s, size_t& i);

static Value parse_array(const std::string& s, size_t& i) {
    Value v = Value::array();
    ++i;  // '['
    skip_ws(s, i);
    if (i < s.size() && s[i] == ']') { ++i; return v; }
    while (i < s.size()) {
        v.arr.push_back(parse_value(s, i));
        skip_ws(s, i);
        if (i < s.size() && s[i] == ',') { ++i; skip_ws(s, i); }
        else if (i < s.size() && s[i] == ']') { ++i; break; }
    }
    return v;
}

static Value parse_object(const std::string& s, size_t& i) {
    Value v = Value::object();
    ++i;  // '{'
    skip_ws(s, i);
    if (i < s.size() && s[i] == '}') { ++i; return v; }
    while (i < s.size()) {
        skip_ws(s, i);
        std::string key;
        if (i < s.size() && s[i] == '"') key = parse_string(s, i);
        skip_ws(s, i);
        if (i < s.size() && s[i] == ':') ++i;
        v.obj[key] = parse_value(s, i);
        skip_ws(s, i);
        if (i < s.size() && s[i] == ',') { ++i; }
        else if (i < s.size() && s[i] == '}') { ++i; break; }
    }
    return v;
}

static Value parse_value(const std::string& s, size_t& i) {
    skip_ws(s, i);
    if (i >= s.size()) return Value::null();
    char c = s[i];
    if (c == '{') return parse_object(s, i);
    if (c == '[') return parse_array(s, i);
    if (c == '"') { Value v = Value::string(parse_string(s, i)); return v; }
    if (c == 't') { i += 4; return Value::boolean(true); }
    if (c == 'f') { i += 5; return Value::boolean(false); }
    if (c == 'n') { i += 4; return Value::null(); }
    // number
    size_t start = i;
    while (i < s.size() && (std::isdigit(static_cast<unsigned char>(s[i]))
                            || s[i] == '-' || s[i] == '+' || s[i] == '.'
                            || s[i] == 'e' || s[i] == 'E')) ++i;
    Value v = Value::number(std::strtold(s.substr(start, i - start).c_str(), nullptr));
    return v;
}

static Value parse(const std::string& s) {
    size_t i = 0;
    return parse_value(s, i);
}

static std::string escape(const std::string& s) {
    std::string out;
    out.reserve(s.size() + 8);
    for (char c : s) {
        switch (c) {
            case '"': out += "\\\""; break;
            case '\\': out += "\\\\"; break;
            case '\n': out += "\\n"; break;
            case '\r': out += "\\r"; break;
            case '\t': out += "\\t"; break;
            default:
                if (static_cast<unsigned char>(c) < 0x20) {
                    char buf[8];
                    std::snprintf(buf, sizeof(buf), "\\u%04x", c);
                    out += buf;
                } else {
                    out += c;
                }
        }
    }
    return out;
}

static std::string stringify(const Value& v) {
    switch (v.type) {
        case Value::NUL: return "null";
        case Value::BOOL: return v.b ? "true" : "false";
        case Value::NUM: {
            char buf[64];
            std::snprintf(buf, sizeof(buf), "%.12Lg", v.num);
            return buf;
        }
        case Value::STR: return "\"" + escape(v.str) + "\"";
        case Value::ARR: {
            std::string out = "[";
            for (size_t k = 0; k < v.arr.size(); ++k) {
                if (k) out += ",";
                out += stringify(v.arr[k]);
            }
            return out + "]";
        }
        case Value::OBJ: {
            std::string out = "{";
            bool first = true;
            for (const auto& kv : v.obj) {
                if (!first) out += ",";
                first = false;
                out += "\"" + escape(kv.first) + "\":" + stringify(kv.second);
            }
            return out + "}";
        }
    }
    return "null";
}

}  // namespace json

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

// Parse a numeric value from a JSON number or a numeric string. Commas are
// the only tolerated decoration; anything else yields false.
static bool parse_number(const json::Value& v, long double& out) {
    if (v.type == json::Value::NUM) { out = v.num; return true; }
    if (v.type != json::Value::STR) return false;
    std::string s = v.str;
    s.erase(std::remove(s.begin(), s.end(), ','), s.end());
    if (s.empty()) return false;
    char* end = nullptr;
    long double val = std::strtold(s.c_str(), &end);
    if (end == s.c_str() || *end != '\0') return false;
    // reject bare labels / booleans-like strings
    if (std::isnan(val) || std::isinf(val)) return false;
    out = val;
    return true;
}

// Extract a 4-digit year (19xx/20xx) from a reporting-period label.
// Returns -1 when the year cannot be determined (never guesses).
static int period_year(const std::string& period) {
    if (period.empty()) return -1;
    for (size_t i = 0; i + 4 <= period.size(); ++i) {
        char a = period[i], b = period[i + 1], c = period[i + 2], d = period[i + 3];
        if ((a == '1' || a == '2') && std::isdigit(static_cast<unsigned char>(b))
            && std::isdigit(static_cast<unsigned char>(c))
            && std::isdigit(static_cast<unsigned char>(d))) {
            return (a - '0') * 1000 + (b - '0') * 100 + (c - '0') * 10 + (d - '0');
        }
    }
    return -1;
}

// Canonical scale label — 'B' == 'billions' == 'billion' etc. Unknown labels
// are compared verbatim (nothing is converted, only synonyms normalized).
static std::string scale_canon(const std::string& raw) {
    std::string s = raw;
    std::transform(s.begin(), s.end(), s.begin(),
                   [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
    static const std::map<std::string, std::string> kSynonyms = {
        {"unit", "unit"}, {"units", "unit"},
        {"thousand", "thousands"}, {"thousands", "thousands"}, {"k", "thousands"},
        {"million", "millions"}, {"millions", "millions"}, {"m", "millions"},
        {"billion", "billions"}, {"billions", "billions"}, {"b", "billions"},
        {"crore", "crores"}, {"crores", "crores"},
    };
    auto it = kSynonyms.find(s);
    return it == kSynonyms.end() ? s : it->second;
}

// Display rounding: round `value` to `precision` decimals and format with
// exactly `precision` digits (trailing zeros preserved, e.g. "1.40").
static std::string format_fixed(long double value, int precision) {
    long double scale = std::pow(10.0L, precision);
    long double r = std::roundl(value * scale) / scale;
    char buf[96];
    std::snprintf(buf, sizeof(buf), "%.*Lf", precision, r);
    return buf;
}

// Display policy: percent-kind metrics show "36.61%", ratio/amount plain.
static std::string display_value(long double raw_value, const FormulaDef& def) {
    long double shown = raw_value;
    if (def.kind == "percent") shown *= 100.0L;
    std::string num = format_fixed(shown, def.precision);
    return def.kind == "percent" ? num + "%" : num;
}

// ---------------------------------------------------------------------------
// Formula Registry — the approved formulas ONLY (legacy 9, unchanged).
// ---------------------------------------------------------------------------

static std::vector<FormulaDef> build_registry() {
    return {
        {"ROE", "ROE", {"Net Profit", "Equity"},
         "Net Profit ÷ Equity × 100", "percent", 2, "same", {"Equity"}},
        {"ROA", "ROA", {"Net Profit", "Assets"},
         "Net Profit ÷ Assets × 100", "percent", 2, "same", {"Assets"}},
        {"Profit Margin", "Profit Margin", {"Net Profit", "Revenue"},
         "Net Profit ÷ Revenue × 100", "percent", 2, "same", {"Revenue"}},
        {"Operating Margin", "Operating Margin", {"Operating Profit", "Revenue"},
         "Operating Profit ÷ Revenue × 100", "percent", 2, "same", {"Revenue"}},
        {"Current Ratio", "Current Ratio", {"Current Assets", "Current Liabilities"},
         "Current Assets ÷ Current Liabilities", "ratio", 2, "same", {"Current Liabilities"}},
        {"Debt to Equity", "Debt to Equity", {"Debt", "Equity"},
         "Debt ÷ Equity", "ratio", 2, "same", {"Equity"}},
        {"Revenue Growth", "Revenue Growth", {"Revenue", "Previous Revenue"},
         "(Revenue − Previous Revenue) ÷ Previous Revenue × 100",
         "percent", 2, "different", {"Previous Revenue"}},
        {"EPS Growth", "EPS Growth", {"EPS", "Previous EPS"},
         "(EPS − Previous EPS) ÷ Previous EPS × 100",
         "percent", 2, "different", {"Previous EPS"}},
        {"CAGR", "CAGR", {"CAGR Beginning Value", "CAGR Ending Value"},
         "(Ending ÷ Beginning) ^ (1 ÷ n) − 1",
         "percent", 2, "span", {"CAGR Beginning Value"}},
    };
}

static const std::vector<FormulaDef>& registry_storage() {
    static const std::vector<FormulaDef> kRegistry = build_registry();
    return kRegistry;
}

const std::vector<FormulaDef>& registry() { return registry_storage(); }

const FormulaDef* registry_lookup(const std::string& metric_key) {
    for (const auto& def : registry_storage()) {
        if (def.key == metric_key) return &def;
    }
    return nullptr;
}

// ---------------------------------------------------------------------------
// Sprint 12A — extended registry (additive; op-driven declarative formulas).
// The legacy registry above is untouched.
// ---------------------------------------------------------------------------
// op semantics (required_inputs[0] <op> required_inputs[1]):
//   sub  target = a - b    inverses: a = target + b ; b = a - target
//   add  target = a + b    inverses: a = target - b ; b = target - a
//   mul  target = a * b    inverses: a = target / b ; b = target / a
//   div  target = a / b    inverses: a = target * b ; b = a / target
// `required_inputs` follows the ORDER OF THE EXPRESSION (left operand
// first), so "Expenses - Revenue" is stored as {"Expenses", "Revenue"}.
// ---------------------------------------------------------------------------

static std::vector<FormulaDef> build_extended_registry() {
    // FormulaDef layout: {key, display_name, required_inputs, formula,
    // kind, precision, period_mode, denominator_inputs, op, target}.
    // `target` is the canonical CONCEPT name (the key under which the
    // formula's output fact appears in the pipeline fact map), which
    // differs from the formula `key` (e.g. PROFIT -> "Profit").
    return {
        {"PROFIT", "Profit", {"Revenue", "Expenses"},
         "Revenue − Expenses", "amount", 2, "same", {}, "sub", "Profit"},
        {"LOSS", "Loss", {"Expenses", "Revenue"},
         "Expenses − Revenue", "amount", 2, "same", {}, "sub", "Loss"},
        {"GROSS_PROFIT", "Gross Profit", {"Revenue", "Cost of Sales"},
         "Revenue − Cost of Sales", "amount", 2, "same", {}, "sub", "Gross Profit"},
        {"WORKING_CAPITAL", "Working Capital", {"Current Assets", "Current Liabilities"},
         "Current Assets − Current Liabilities", "amount", 2, "same", {}, "sub", "Working Capital"},
        {"ASSET_TURNOVER", "Asset Turnover", {"Revenue", "Assets"},
         "Revenue ÷ Assets", "ratio", 2, "same", {"Assets"}, "div", "Asset Turnover"},
        {"EQUITY_MULTIPLIER", "Equity Multiplier", {"Assets", "Equity"},
         "Assets ÷ Equity", "ratio", 2, "same", {"Equity"}, "div", "Equity Multiplier"},
        {"PROFIT_MARGIN", "Profit Margin (P&L)", {"Profit", "Revenue"},
         "Profit ÷ Revenue", "percent", 2, "same", {"Revenue"}, "div", "Profit Margin"},
        // ---- Sprint 12F — production coverage extension (additive) ----
        // ROA over Total Assets (the 12C/12D registry vocabulary; the
        // legacy ROA above uses "Assets" and is left untouched).
        {"ROA_TOTAL_ASSETS", "ROA", {"Net Profit", "Total Assets"},
         "Net Profit ÷ Total Assets × 100", "percent", 2, "same",
         {"Total Assets"}, "div", "ROA"},
        {"GROSS_MARGIN", "Gross Margin", {"Gross Profit", "Revenue"},
         "Gross Profit ÷ Revenue × 100", "percent", 2, "same",
         {"Revenue"}, "div", "Gross Margin"},
        {"EBITDA_MARGIN", "EBITDA Margin", {"EBITDA", "Revenue"},
         "EBITDA ÷ Revenue × 100", "percent", 2, "same",
         {"Revenue"}, "div", "EBITDA Margin"},
        {"NET_MARGIN", "Net Margin", {"Net Profit", "Revenue"},
         "Net Profit ÷ Revenue × 100", "percent", 2, "same",
         {"Revenue"}, "div", "Net Margin"},
        {"EPS", "EPS", {"Net Profit", "Shares Outstanding"},
         "Net Profit ÷ Shares Outstanding", "amount", 2, "same",
         {"Shares Outstanding"}, "div", "EPS"},
        {"DEBT_TO_ASSETS", "Debt to Assets", {"Debt", "Total Assets"},
         "Debt ÷ Total Assets", "ratio", 2, "same",
         {"Total Assets"}, "div", "Debt to Assets"},
        {"INTEREST_COVERAGE", "Interest Coverage",
         {"Operating Profit", "Interest Expense"},
         "Operating Profit ÷ Interest Expense", "ratio", 2, "same",
         {"Interest Expense"}, "div", "Interest Coverage"},
        {"INVENTORY_TURNOVER", "Inventory Turnover",
         {"Cost of Sales", "Average Inventory"},
         "Cost of Sales ÷ Average Inventory", "ratio", 2, "same",
         {"Average Inventory"}, "div", "Inventory Turnover"},
        {"RECEIVABLES_TURNOVER", "Receivables Turnover",
         {"Revenue", "Average Receivables"},
         "Revenue ÷ Average Receivables", "ratio", 2, "same",
         {"Average Receivables"}, "div", "Receivables Turnover"},
        {"PAYABLES_TURNOVER", "Payables Turnover",
         {"Cost of Sales", "Average Payables"},
         "Cost of Sales ÷ Average Payables", "ratio", 2, "same",
         {"Average Payables"}, "div", "Payables Turnover"},
        // Quick Ratio = (Current Assets - Inventory) / Current Liabilities.
        // No binary op (3 inputs); dedicated compute + registered-inverse
        // branches (Current Assets and Current Liabilities only).
        {"QUICK_RATIO", "Quick Ratio",
         {"Current Assets", "Inventory", "Current Liabilities"},
         "(Current Assets − Inventory) ÷ Current Liabilities", "ratio", 2,
         "same", {"Current Liabilities"}, "", "Quick Ratio"},
        // DuPont chain (12B registry vocabulary: Total Assets).
        {"DUPONT_PROFIT_MARGIN", "Profit Margin", {"Net Profit", "Revenue"},
         "Net Profit ÷ Revenue (fraction)", "ratio", 2, "same",
         {"Revenue"}, "div", "Profit Margin"},
        {"DUPONT_ASSET_TURNOVER", "Asset Turnover",
         {"Revenue", "Total Assets"},
         "Revenue ÷ Total Assets", "ratio", 2, "same",
         {"Total Assets"}, "div", "Asset Turnover"},
        {"DUPONT_EQUITY_MULTIPLIER", "Equity Multiplier",
         {"Total Assets", "Equity"},
         "Total Assets ÷ Equity", "ratio", 2, "same",
         {"Equity"}, "div", "Equity Multiplier"},
        {"DUPONT_ROE", "Return on Equity",
         {"Profit Margin", "Asset Turnover", "Equity Multiplier"},
         "Profit Margin × Asset Turnover × Equity Multiplier", "percent",
         2, "same", {}, "", "Return on Equity"},
        // Registered algebraic opposites (12D): Profit = -Loss, Loss = -Profit.
        {"PROFIT_LOSS_OPPOSITE", "Profit", {"Loss"},
         "− Loss", "amount", 2, "same", {}, "neg", "Profit"},
        {"LOSS_PROFIT_OPPOSITE", "Loss", {"Profit"},
         "− Profit", "amount", 2, "same", {}, "neg", "Loss"},
    };
}

static const std::vector<FormulaDef>& extended_registry_storage() {
    static const std::vector<FormulaDef> kExtended = build_extended_registry();
    return kExtended;
}

const std::vector<FormulaDef>& extended_registry() { return extended_registry_storage(); }

// ---------------------------------------------------------------------------
// Sprint 15D — FYJC commercial-arithmetic registry (additive).
// `--registry` (9) and `--registry-ext` (24) contracts are UNCHANGED; these
// FYJC formulas live in their own registry surfaced by `--registry-fyjc`.
// The op-driven entries reuse the generic arithmetic + registered-inverse
// machinery; the rate formulas (a × b ÷ 100, where the rate is a
// percent-number as FYJC textbooks state it) use dedicated compute and
// inverse branches below.
// ---------------------------------------------------------------------------
static std::vector<FormulaDef> build_fyjc_registry() {
    return {
        {"COMMISSION", "Commission", {"Sales", "Commission Rate"},
         "Sales × Commission Rate ÷ 100", "amount", 2, "same", {}, "",
         "Commission"},
        {"TRADE_DISCOUNT", "Trade Discount", {"List Price", "Trade Discount Rate"},
         "List Price × Trade Discount Rate ÷ 100", "amount", 2, "same", {}, "",
         "Trade Discount"},
        {"CASH_DISCOUNT", "Cash Discount", {"Paid Amount", "Cash Discount Rate"},
         "Paid Amount × Cash Discount Rate ÷ 100", "amount", 2, "same", {}, "",
         "Cash Discount"},
        {"NET_PRICE", "Net Price", {"List Price", "Trade Discount"},
         "List Price − Trade Discount", "amount", 2, "same", {}, "sub",
         "Net Price"},
        {"CASH_PAID", "Cash Paid", {"Paid Amount", "Cash Discount"},
         "Paid Amount − Cash Discount", "amount", 2, "same", {}, "sub",
         "Cash Paid"},
        {"CREDITOR_BALANCE", "Creditor Balance", {"Net Purchase", "Amount Paid"},
         "Net Purchase − Amount Paid", "amount", 2, "same", {}, "sub",
         "Creditor Balance"},
        {"DEBTOR_BALANCE", "Debtor Balance", {"Net Sale", "Amount Received"},
         "Net Sale − Amount Received", "amount", 2, "same", {}, "sub",
         "Debtor Balance"},
        {"SELLING_PRICE", "Selling Price", {"Cost Price", "Profit"},
         "Cost Price + Profit", "amount", 2, "same", {}, "add",
         "Selling Price"},
        {"PROFIT_PERCENT", "Profit Percent", {"Profit", "Cost Price"},
         "Profit ÷ Cost Price × 100", "percent", 2, "same", {"Cost Price"},
         "div", "Profit Percent"},
        {"LOSS_PERCENT", "Loss Percent", {"Loss", "Cost Price"},
         "Loss ÷ Cost Price × 100", "percent", 2, "same", {"Cost Price"},
         "div", "Loss Percent"},
    };
}

static const std::vector<FormulaDef>& fyjc_registry_storage() {
    static const std::vector<FormulaDef> kFyjc = build_fyjc_registry();
    return kFyjc;
}

const std::vector<FormulaDef>& fyjc_registry() { return fyjc_registry_storage(); }

static const FormulaDef* fyjc_lookup(const std::string& metric_key) {
    for (const auto& def : fyjc_registry_storage()) {
        if (def.key == metric_key) return &def;
    }
    return nullptr;
}

// Look up legacy first, then extended, then FYJC (additive; legacy wins on
// key collisions so existing behavior is never shadowed).
static const FormulaDef* lookup_any(const std::string& metric_key) {
    const FormulaDef* def = registry_lookup(metric_key);
    if (def != nullptr) return def;
    for (const auto& d : extended_registry_storage()) {
        if (d.key == metric_key) return &d;
    }
    return fyjc_lookup(metric_key);
}

std::string status_name(Status s) {
    switch (s) {
        case Status::REPORTED_VERIFIED: return "REPORTED_VERIFIED";
        case Status::DERIVED_VERIFIED: return "DERIVED_VERIFIED";
        case Status::EXTERNAL_DERIVED: return "EXTERNAL_DERIVED";
        case Status::BLOCKED: return "BLOCKED";
        case Status::UNANALYZED: return "UNANALYZED";
    }
    return "UNANALYZED";
}

static Result blocked(std::string reason) {
    Result r;
    r.status = Status::BLOCKED;
    r.block_reason = std::move(reason);
    return r;
}

// ---------------------------------------------------------------------------
// Validation (mirrors the Python engine's deterministic rules; reasons are
// kept text-compatible so the existing Python tests keep passing).
// ---------------------------------------------------------------------------

// Returns the CAGR span n (end_year - begin_year) or -1 when unusable.
// Sprint 12F: an explicit "CAGR Span Years" fact wins when present and
// valid (never guessed); otherwise the span is derived from the two
// reporting periods (existing behavior, unchanged).
static int cagr_span(const std::map<std::string, Fact>& facts) {
    auto it_s = facts.find("CAGR Span Years");
    if (it_s != facts.end() && it_s->second.has_value
        && it_s->second.value >= 1.0L
        && it_s->second.value == std::floor(it_s->second.value)
        && it_s->second.value <= 1000.0L) {
        return static_cast<int>(it_s->second.value);
    }
    auto it_b = facts.find("CAGR Beginning Value");
    auto it_e = facts.find("CAGR Ending Value");
    if (it_b == facts.end() || it_e == facts.end()) return -1;
    int by = period_year(it_b->second.reporting_period);
    int ey = period_year(it_e->second.reporting_period);
    if (by < 0 || ey < 0) return -1;
    int n = ey - by;
    return n >= 1 ? n : -1;
}

static Result validate(const FormulaDef& def,
                       const std::map<std::string, Fact>& facts) {
    // 1) every required input must exist and be numeric
    for (const auto& key : def.required_inputs) {
        auto it = facts.find(key);
        if (it == facts.end() || !it->second.has_value) {
            return blocked(key + " is unavailable from permitted evidence sources.");
        }
    }
    // 2) denominator must not be zero
    for (const auto& dk : def.denominator_inputs) {
        auto it = facts.find(dk);
        if (it != facts.end() && it->second.has_value && it->second.value == 0.0L) {
            return blocked(dk + " is zero — " + def.display_name +
                           " cannot be calculated.");
        }
    }
    // 3) currency compatibility (reject incompatible; never convert)
    std::set<std::string> currencies;
    for (const auto& key : def.required_inputs) {
        const std::string& u = facts.at(key).unit;
        if (!u.empty()) currencies.insert(u);
    }
    if (currencies.size() > 1) {
        std::string joined;
        for (const auto& c : currencies) {
            if (!joined.empty()) joined += ", ";
            joined += c;
        }
        return blocked("Currency mismatch between inputs (" + joined + ").");
    }
    // 4) scale compatibility (synonym-normalized)
    std::set<std::string> scales;
    for (const auto& key : def.required_inputs) {
        const std::string& s = facts.at(key).scale;
        if (!s.empty()) scales.insert(scale_canon(s));
    }
    if (scales.size() > 1) {
        std::string joined;
        for (const auto& s : scales) {
            if (!joined.empty()) joined += ", ";
            joined += s;
        }
        return blocked("Scale mismatch between inputs (" + joined + ").");
    }
    // 5) period requirements
    if (def.period_mode == "same") {
        std::set<std::string> periods;
        for (const auto& key : def.required_inputs) {
            const std::string& p = facts.at(key).reporting_period;
            if (!p.empty()) periods.insert(p);
        }
        if (periods.size() > 1) {
            std::string joined;
            for (const auto& p : periods) {
                if (!joined.empty()) joined += ", ";
                joined += p;
            }
            return blocked("Incompatible reporting periods for " + def.display_name +
                           " (" + joined + ").");
        }
    } else if (def.period_mode == "different") {
        const std::string& p0 = facts.at(def.required_inputs[0]).reporting_period;
        const std::string& p1 = facts.at(def.required_inputs[1]).reporting_period;
        if (!p0.empty() && !p1.empty() && p0 == p1) {
            return blocked(def.display_name +
                           " needs two different reporting periods (both are " +
                           p0 + ").");
        }
    }
    if (def.key == "CAGR") {
        if (cagr_span(facts) < 0) {
            return blocked("CAGR period span cannot be determined from input reporting periods.");
        }
        if (facts.at("CAGR Beginning Value").value <= 0.0L
            || facts.at("CAGR Ending Value").value <= 0.0L) {
            return blocked("CAGR requires positive beginning and ending values.");
        }
    }
    Result ok;
    ok.status = Status::DERIVED_VERIFIED;
    return ok;
}

// ---------------------------------------------------------------------------
// Deterministic arithmetic (long double; display rounding at output only)
// ---------------------------------------------------------------------------

// Sprint 12A — generic op-driven arithmetic for the extended registry.
// Sprint 12F: supports unary "neg" and N-input "mul" (for the DuPont
// 3-factor chain) in addition to the binary sub/add/div.
static Result compute_generic(const FormulaDef& def,
                              const std::map<std::string, Fact>& facts) {
    auto val = [&facts](const std::string& k) {
        return facts.at(k).value;
    };
    long double raw = 0.0L;
    if (def.op == "neg") {
        raw = -val(def.required_inputs[0]);
    } else if (def.op == "sub") {
        raw = val(def.required_inputs[0]) - val(def.required_inputs[1]);
    } else if (def.op == "add") {
        raw = val(def.required_inputs[0]) + val(def.required_inputs[1]);
    } else if (def.op == "mul") {
        raw = val(def.required_inputs[0]);
        for (size_t i = 1; i < def.required_inputs.size(); ++i) {
            raw *= val(def.required_inputs[i]);
        }
    } else if (def.op == "div") {
        raw = val(def.required_inputs[0]) / val(def.required_inputs[1]);
        // zero denominator already rejected in validate()
    } else {
        return blocked("unsupported operation: " + def.op);
    }
    Result r;
    r.status = Status::DERIVED_VERIFIED;
    r.value = raw;
    r.has_value = true;
    r.display_value = display_value(raw, def);
    r.calculation_steps.push_back(def.display_name + " = " + def.formula);
    r.calculation_steps.push_back(def.display_name + " = " + r.display_value);
    return r;
}

static Result compute(const FormulaDef& def,
                      const std::map<std::string, Fact>& facts) {
    // Sprint 12A — op-driven formulas take the generic arithmetic path.
    if (!def.op.empty()) {
        return compute_generic(def, facts);
    }
    auto val = [&facts](const std::string& k) {
        return facts.at(k).value;
    };

    long double raw = 0.0L;  // fraction for percent-kind, ratio otherwise
    std::vector<std::string> steps;

    if (def.key == "CAGR") {
        long double bv = val("CAGR Beginning Value");
        long double ev = val("CAGR Ending Value");
        int n = cagr_span(facts);
        raw = std::pow(ev / bv, 1.0L / static_cast<long double>(n)) - 1.0L;
        steps.push_back("CAGR = (Ending ÷ Beginning) ^ (1 ÷ n) − 1");
        steps.push_back("n = " + std::to_string(n) + " year(s)");
        steps.push_back("CAGR = " + display_value(raw, def));
    } else if (def.key == "Revenue Growth") {
        raw = val("Revenue") / val("Previous Revenue") - 1.0L;
        steps.push_back(def.display_name + " = " + def.formula);
        steps.push_back(def.display_name + " = " + display_value(raw, def));
    } else if (def.key == "EPS Growth") {
        raw = val("EPS") / val("Previous EPS") - 1.0L;
        steps.push_back(def.display_name + " = " + def.formula);
        steps.push_back(def.display_name + " = " + display_value(raw, def));
    } else if (def.key == "ROE" || def.key == "ROA" || def.key == "Profit Margin"
               || def.key == "Operating Margin") {
        const std::string& num = def.required_inputs[0];
        const std::string& den = def.required_inputs[1];
        raw = val(num) / val(den);
        steps.push_back(def.display_name + " = " + def.formula);
        steps.push_back(def.display_name + " = " + display_value(raw, def));
    } else if (def.key == "QUICK_RATIO") {
        // Quick Ratio = (Current Assets - Inventory) / Current Liabilities
        raw = (val("Current Assets") - val("Inventory"))
            / val("Current Liabilities");
        steps.push_back(def.display_name + " = " + def.formula);
        steps.push_back(def.display_name + " = " + display_value(raw, def));
    } else if (def.key == "DUPONT_ROE") {
        // DuPont ROE = Profit Margin x Asset Turnover x Equity Multiplier
        raw = val("Profit Margin") * val("Asset Turnover")
            * val("Equity Multiplier");
        steps.push_back(def.display_name + " = " + def.formula);
        steps.push_back(def.display_name + " = " + display_value(raw, def));
    } else if (def.key == "COMMISSION" || def.key == "TRADE_DISCOUNT"
               || def.key == "CASH_DISCOUNT") {
        // Sprint 15D — FYJC rate formula: inputs[0] × inputs[1] ÷ 100
        // (the rate is a percent-number, e.g. 5 for 5%).
        raw = val(def.required_inputs[0]) * val(def.required_inputs[1])
            / 100.0L;
        steps.push_back(def.display_name + " = " + def.formula);
        steps.push_back(def.display_name + " = " + display_value(raw, def));
    } else {  // ratio-kind: Current Ratio, Debt to Equity
        const std::string& num = def.required_inputs[0];
        const std::string& den = def.required_inputs[1];
        raw = val(num) / val(den);
        steps.push_back(def.display_name + " = " + def.formula);
        steps.push_back(def.display_name + " = " + display_value(raw, def));
    }

    Result r;
    r.status = Status::DERIVED_VERIFIED;
    r.value = raw;  // full deterministic precision (fraction)
    r.has_value = true;
    r.display_value = display_value(raw, def);
    r.calculation_steps = std::move(steps);
    return r;
}

// ---------------------------------------------------------------------------
// Provenance: EXTERNAL_DERIVED when any input came through Sprint 6.5
// (REGULATORY_API or APPENDIX). Pure DERIVED_VERIFIED otherwise.
// ---------------------------------------------------------------------------
static bool is_external_tier(const std::string& t) {
    return t == "REGULATORY_API" || t == "APPENDIX";
}

// ---------------------------------------------------------------------------
// Lineage tree (auditable, preserves every input's provenance)
// ---------------------------------------------------------------------------
static std::string build_lineage(const FormulaDef& def,
                                 const std::map<std::string, Fact>& facts,
                                 const std::string& display) {
    std::ostringstream os;
    os << def.display_name << "\n";
    os << "├── Formula: " << def.formula << "\n";
    size_t i = 0;
    for (const auto& key : def.required_inputs) {
        const Fact& f = facts.at(key);
        bool last = (i + 1 == def.required_inputs.size());
        const char* branch = last ? "└──" : "├──";
        os << branch << " " << key << "\n";
        const char* pad = last ? "    " : "│   ";
        std::string dv = format_fixed(f.value, 2);
        os << pad << "├── Value: " << dv << "\n";
        os << pad << "├── Provenance: "
           << (f.provenance_tier.empty() ? "DOCUMENT" : f.provenance_tier) << "\n";
        if (!f.page.empty()) {
            os << pad << "├── Page: " << f.page << "\n";
        }
        if (!f.evidence.empty()) {
            os << pad << (f.page.empty() ? "└──" : "├──")
               << " Evidence: " << f.evidence << "\n";
        }
        ++i;
    }
    os << "└── Result: " << display << "\n";
    return os.str();
}

// ---------------------------------------------------------------------------
// Core entry point
// ---------------------------------------------------------------------------

Result calculate_metric(const std::string& metric_key,
                        const std::map<std::string, Fact>& facts) {
    // Sprint 12A — extended formulas are reachable through the same entry
    // point; the legacy 9 keep their dedicated path (additive only).
    const FormulaDef* def = lookup_any(metric_key);
    if (def == nullptr) {
        Result r;
        r.status = Status::UNANALYZED;
        return r;
    }

    Result v = validate(*def, facts);
    if (v.status == Status::BLOCKED) return v;

    Result c = compute(*def, facts);
    if (!c.has_value) {
        return blocked(def->display_name + " cannot be calculated.");
    }

    bool external = false;
    for (const auto& key : def->required_inputs) {
        const auto& tier = facts.at(key).provenance_tier;
        if (is_external_tier(tier)) { external = true; break; }
    }
    c.status = external ? Status::EXTERNAL_DERIVED : Status::DERIVED_VERIFIED;
    c.lineage = build_lineage(*def, facts, c.display_value);
    return c;
}

// ---------------------------------------------------------------------------
// Sprint 12A — reverse solving for op-driven formulas.
// Only mathematically valid, unambiguous algebraic inverses are applied;
// anything else is BLOCKED. Percent-kind formulas store their target as a
// percentage number, so the inverse converts it back to a fraction
// (target_value / 100) before it is used as a factor.
// ---------------------------------------------------------------------------

Result solve_metric(const std::string& metric_key,
                    const std::string& solve_for,
                    const std::map<std::string, Fact>& facts) {
    const FormulaDef* def = lookup_any(metric_key);
    if (def == nullptr) {
        Result r;
        r.status = Status::UNANALYZED;
        return r;
    }
    // The formula's target CONCEPT (key under which the pipeline stores
    // the output fact) - falls back to the formula key for formulas that
    // did not declare a distinct target.
    const std::string target_key = def->target.empty() ? def->key : def->target;

    auto need = [&facts](const std::string& k) -> bool {
        auto it = facts.find(k);
        return it != facts.end() && it->second.has_value;
    };
    auto val = [&facts](const std::string& k) -> long double {
        return facts.at(k).value;
    };
    // Target value, converted to a fraction for percent-kind formulas.
    auto target_value = [&]() -> long double {
        long double tv = val(target_key);
        if (def->kind == "percent") tv /= 100.0L;
        return tv;
    };

    if (def->op.empty()) {
        // Legacy 2-input percent/ratio formulas whose inverse
        // relationships ARE registered in the Python registry (ROE, ROA,
        // Profit Margin, Operating Margin, Current Ratio, Debt to Equity):
        // the C++ authority executes those inverses. Growth/CAGR formulas
        // are excluded - their inverse is not the plain a/b form and no
        // inverse is registered for them.
        {
            static const std::set<std::string> kLegacyInvertible = {
                "ROE", "ROA", "Profit Margin", "Operating Margin",
                "Current Ratio", "Debt to Equity",
            };
            if (kLegacyInvertible.count(def->key) > 0
                && def->required_inputs.size() == 2) {
                const std::string& a = def->required_inputs[0];
                const std::string& b = def->required_inputs[1];
                if (solve_for == def->key || solve_for == target_key) {
                    if (!need(a) || !need(b)) {
                        return blocked(target_key + " is unavailable from "
                                       "permitted evidence sources.");
                    }
                    return compute(*def, facts);
                }
                if (solve_for == a) {
                    if (!need(target_key) || !need(b)) {
                        return blocked("Cannot solve for " + solve_for +
                                       ": required input is unavailable "
                                       "from permitted evidence sources.");
                    }
                    if (val(b) == 0.0L) {
                        return blocked(b + " is zero - cannot solve for " +
                                       a + ".");
                    }
                    Result r;
                    r.status = Status::DERIVED_VERIFIED;
                    r.value = target_value() * val(b);
                    r.has_value = true;
                    r.display_value = format_fixed(r.value, def->precision);
                    r.calculation_steps.push_back(
                        "solve " + solve_for + " from " + def->display_name +
                        " = " + def->formula);
                    r.calculation_steps.push_back(solve_for + " = " +
                                                  r.display_value);
                    return r;
                }
                if (solve_for == b) {
                    if (!need(target_key) || !need(a)) {
                        return blocked("Cannot solve for " + solve_for +
                                       ": required input is unavailable "
                                       "from permitted evidence sources.");
                    }
                    if (target_value() == 0.0L) {
                        return blocked(target_key + " is zero - cannot "
                                       "solve for " + b + ".");
                    }
                    Result r;
                    r.status = Status::DERIVED_VERIFIED;
                    r.value = val(a) / target_value();
                    r.has_value = true;
                    r.display_value = format_fixed(r.value, def->precision);
                    r.calculation_steps.push_back(
                        "solve " + solve_for + " from " + def->display_name +
                        " = " + def->formula);
                    r.calculation_steps.push_back(solve_for + " = " +
                                                  r.display_value);
                    return r;
                }
                return blocked(solve_for + " is not a variable of " +
                               metric_key + ".");
            }
        }
        // Sprint 12F - QUICK_RATIO carries REGISTERED inverse
        // relationships (Current Assets, Current Liabilities) even though
        // it is not a binary op; every other op-less formula (e.g.
        // DUPONT_ROE) has no registered inverse and fails closed.
        if (def->key == "QUICK_RATIO") {
            const std::string qr_target = "Quick Ratio";
            if (solve_for == def->key || solve_for == qr_target) {
                Result fwd = compute(*def, facts);
                return fwd.has_value ? fwd
                    : blocked(qr_target + " is unavailable from permitted "
                              "evidence sources.");
            }
            if (solve_for == "Current Assets") {
                if (!need(qr_target) || !need("Current Liabilities")
                    || !need("Inventory")) {
                    return blocked("Cannot solve for Current Assets: required "
                                   "input is unavailable from permitted "
                                   "evidence sources.");
                }
                Result r;
                r.status = Status::DERIVED_VERIFIED;
                r.value = val(qr_target) * val("Current Liabilities")
                    + val("Inventory");
                r.has_value = true;
                r.display_value = format_fixed(r.value, def->precision);
                r.calculation_steps.push_back(
                    "solve Current Assets from Quick Ratio = "
                    "(Current Assets - Inventory) / Current Liabilities");
                r.calculation_steps.push_back(
                    "Current Assets = " + r.display_value);
                return r;
            }
            if (solve_for == "Current Liabilities") {
                if (!need(qr_target) || !need("Current Assets")
                    || !need("Inventory")) {
                    return blocked("Cannot solve for Current Liabilities: "
                                   "required input is unavailable from "
                                   "permitted evidence sources.");
                }
                if (val(qr_target) == 0.0L) {
                    return blocked("Quick Ratio is zero - cannot solve for "
                                   "Current Liabilities.");
                }
                Result r;
                r.status = Status::DERIVED_VERIFIED;
                r.value = (val("Current Assets") - val("Inventory"))
                    / val(qr_target);
                r.has_value = true;
                r.display_value = format_fixed(r.value, def->precision);
                r.calculation_steps.push_back(
                    "solve Current Liabilities from Quick Ratio = "
                    "(Current Assets - Inventory) / Current Liabilities");
                r.calculation_steps.push_back(
                    "Current Liabilities = " + r.display_value);
                return r;
            }
            return blocked(solve_for + " is not a variable of Quick Ratio.");
        }
        // Sprint 15D - FYJC rate formulas (a × b ÷ 100) carry REGISTERED
        // inverse relationships: a = target × 100 ÷ b ; b = target × 100 ÷ a.
        // The target is an amount (never percent), so no fraction
        // conversion is needed.
        if (def->key == "COMMISSION" || def->key == "TRADE_DISCOUNT"
            || def->key == "CASH_DISCOUNT") {
            const std::string& a = def->required_inputs[0];
            const std::string& b = def->required_inputs[1];
            if (solve_for == def->key || solve_for == target_key) {
                if (!need(a) || !need(b)) {
                    return blocked(target_key + " is unavailable from "
                                   "permitted evidence sources.");
                }
                return compute(*def, facts);
            }
            if (solve_for == a) {
                if (!need(target_key) || !need(b)) {
                    return blocked("Cannot solve for " + solve_for +
                                   ": required input is unavailable from "
                                   "permitted evidence sources.");
                }
                if (val(b) == 0.0L) {
                    return blocked(b + " is zero - cannot solve for " +
                                   a + ".");
                }
                Result r;
                r.status = Status::DERIVED_VERIFIED;
                r.value = val(target_key) * 100.0L / val(b);
                r.has_value = true;
                r.display_value = format_fixed(r.value, def->precision);
                r.calculation_steps.push_back(
                    "solve " + solve_for + " from " + def->display_name +
                    " = " + def->formula);
                r.calculation_steps.push_back(solve_for + " = " +
                                              r.display_value);
                return r;
            }
            if (solve_for == b) {
                if (!need(target_key) || !need(a)) {
                    return blocked("Cannot solve for " + solve_for +
                                   ": required input is unavailable from "
                                   "permitted evidence sources.");
                }
                if (val(a) == 0.0L) {
                    return blocked(a + " is zero - cannot solve for " +
                                   b + ".");
                }
                Result r;
                r.status = Status::DERIVED_VERIFIED;
                r.value = val(target_key) * 100.0L / val(a);
                r.has_value = true;
                r.display_value = format_fixed(r.value, def->precision);
                r.calculation_steps.push_back(
                    "solve " + solve_for + " from " + def->display_name +
                    " = " + def->formula);
                r.calculation_steps.push_back(solve_for + " = " +
                                              r.display_value);
                return r;
            }
            return blocked(solve_for + " is not a variable of " +
                           metric_key + ".");
        }
        return blocked("No registered inverse relationship for " + solve_for +
                       " in " + metric_key + ".");
    }

    // Unary opposites (Sprint 12F): Profit = -Loss and Loss = -Profit have
    // one input and the registered inverse input = -target.
    if (def->op == "neg") {
        const std::string& only = def->required_inputs[0];
        if (solve_for == def->key || solve_for == target_key) {
            if (!need(only)) {
                return blocked(target_key + " is unavailable from permitted "
                               "evidence sources.");
            }
            return compute_generic(*def, facts);
        }
        if (solve_for == only) {
            if (!need(target_key)) {
                return blocked("Cannot solve for " + solve_for + ": required "
                               "input is unavailable from permitted evidence "
                               "sources.");
            }
            Result r;
            r.status = Status::DERIVED_VERIFIED;
            r.value = -val(target_key);
            r.has_value = true;
            r.display_value = format_fixed(r.value, def->precision);
            r.calculation_steps.push_back(
                "solve " + solve_for + " from " + def->display_name + " = " +
                def->formula);
            r.calculation_steps.push_back(solve_for + " = " + r.display_value);
            r.lineage = solve_for + " (solved from " + def->display_name +
                        " = " + def->formula + ")";
            return r;
        }
        return blocked(solve_for + " is not a variable of " + metric_key + ".");
    }

    const std::string& a = def->required_inputs[0];
    const std::string& b = def->required_inputs[1];

    // Forward direction: solving for the formula's own target concept
    // (matched by concept name when declared, else by formula key).
    if (solve_for == def->key || solve_for == target_key) {
        if (!need(a) || !need(b)) {
            return blocked(target_key + " is unavailable from permitted evidence sources.");
        }
        return compute_generic(*def, facts);
    }

    long double result = 0.0L;
    if (solve_for == a) {
        if (!need(target_key) || !need(b)) {
            return blocked("Cannot solve for " + solve_for + ": required input is unavailable from permitted evidence sources.");
        }
        if (def->op == "sub") {
            result = target_value() + val(b);
        } else if (def->op == "add") {
            result = target_value() - val(b);
        } else if (def->op == "mul") {
            if (val(b) == 0.0L) {
                return blocked(b + " is zero - cannot solve for " + a + ".");
            }
            result = target_value() / val(b);
        } else if (def->op == "div") {
            result = target_value() * val(b);
        } else {
            return blocked("unsupported operation: " + def->op);
        }
    } else if (solve_for == b) {
        if (!need(target_key) || !need(a)) {
            return blocked("Cannot solve for " + solve_for + ": required input is unavailable from permitted evidence sources.");
        }
        if (def->op == "sub") {
            result = val(a) - target_value();
        } else if (def->op == "add") {
            result = target_value() - val(a);
        } else if (def->op == "mul") {
            if (val(a) == 0.0L) {
                return blocked(a + " is zero - cannot solve for " + b + ".");
            }
            result = target_value() / val(a);
        } else if (def->op == "div") {
            if (target_value() == 0.0L) {
                return blocked(target_key + " is zero - cannot solve for " + b + ".");
            }
            result = val(a) / target_value();
        } else {
            return blocked("unsupported operation: " + def->op);
        }
    } else {
        return blocked(solve_for + " is not a variable of " + metric_key + ".");
    }

    Result r;
    r.status = Status::DERIVED_VERIFIED;
    r.value = result;
    r.has_value = true;
    // Solved variables are plain amounts/ratios — never percent display.
    r.display_value = format_fixed(result, def->precision);
    r.calculation_steps.push_back("solve " + solve_for + " from " +
                                  def->display_name + " = " + def->formula);
    r.calculation_steps.push_back(solve_for + " = " + r.display_value);
    std::ostringstream os;
    os << solve_for << " (solved from " << def->display_name << " = "
       << def->formula << ")\n";
    for (const auto& kv : facts) {
        os << "├── " << kv.first << " = "
           << format_fixed(kv.second.value, 2) << "\n";
    }
    os << "└── Result: " << r.display_value << "\n";
    r.lineage = os.str();
    return r;
}

// ---------------------------------------------------------------------------
// JSON input parsing (Fact map) and CLI runner
// ---------------------------------------------------------------------------

static Fact parse_fact(const json::Value& obj) {
    Fact f;
    if (const json::Value* v = obj.get("metric")) f.metric = v->str;
    if (const json::Value* v = obj.get("unit")) f.unit = v->str;
    if (const json::Value* v = obj.get("scale")) f.scale = v->str;
    if (const json::Value* v = obj.get("reporting_period")) f.reporting_period = v->str;
    if (const json::Value* v = obj.get("provenance_tier")) f.provenance_tier = v->str;
    if (const json::Value* v = obj.get("document_name")) f.document_name = v->str;
    if (const json::Value* v = obj.get("page")) f.page = v->str;
    if (const json::Value* v = obj.get("evidence")) f.evidence = v->str;
    if (const json::Value* v = obj.get("provider")) f.provider = v->str;
    if (const json::Value* v = obj.get("source_ref")) f.source_ref = v->str;
    long double val = 0.0L;
    if (const json::Value* v = obj.get("value")) {
        if (parse_number(*v, val)) {
            f.value = val;
            f.has_value = true;
        }
    }
    return f;
}

std::string run_cli(const std::string& stdin_json) {
    json::Value root = json::parse(stdin_json);

    const json::Value* metric_v = root.get("metric");
    if (metric_v == nullptr || metric_v->type != json::Value::STR) {
        json::Value err = json::object();
        err.obj["error"] = json::string("missing or invalid 'metric'");
        return json::stringify(err);
    }
    std::string metric_key = metric_v->str;

    std::string solve_for;
    if (const json::Value* s = root.get("solve_for")) {
        if (s->type == json::Value::STR) solve_for = s->str;
    }

    std::map<std::string, Fact> facts;
    if (const json::Value* inputs = root.get("inputs")) {
        if (inputs->type == json::Value::OBJ) {
            for (const auto& kv : inputs->obj) {
                if (kv.second.type == json::Value::OBJ) {
                    facts[kv.first] = parse_fact(kv.second);
                }
            }
        }
    }

    Result r = solve_for.empty()
        ? calculate_metric(metric_key, facts)
        : solve_metric(metric_key, solve_for, facts);

    json::Value out = json::object();
    out.obj["status"] = json::string(status_name(r.status));
    out.obj["metric_key"] = json::string(metric_key);
    if (r.has_value) {
        out.obj["value"] = json::number(r.value);
        out.obj["display_value"] = json::string(r.display_value);
    } else {
        out.obj["value"] = json::null();
        out.obj["display_value"] = json::string("");
    }
    json::Value steps = json::array();
    for (const auto& s : r.calculation_steps) steps.arr.push_back(json::string(s));
    out.obj["calculation_steps"] = steps;
    out.obj["lineage"] = json::string(r.lineage);
    out.obj["block_reason"] = r.block_reason.empty()
        ? json::null()
        : json::string(r.block_reason);
    return json::stringify(out);
}

// `--registry`: legacy formula metadata as JSON (exactly the legacy 9).
static std::string registry_json() {
    json::Value arr = json::array();
    for (const auto& def : registry()) {
        json::Value o = json::object();
        o.obj["metric_key"] = json::string(def.key);
        o.obj["display_name"] = json::string(def.display_name);
        o.obj["formula"] = json::string(def.formula);
        o.obj["unit"] = json::string(def.kind);
        o.obj["precision"] = json::number(def.precision);
        o.obj["period_mode"] = json::string(def.period_mode);
        json::Value req = json::array();
        for (const auto& k : def.required_inputs) req.arr.push_back(json::string(k));
        o.obj["required_inputs"] = req;
        arr.arr.push_back(o);
    }
    return json::stringify(arr);
}

// `--registry-ext`: Sprint 12A extended formula metadata as JSON.
static std::string extended_registry_json() {
    json::Value arr = json::array();
    for (const auto& def : extended_registry()) {
        json::Value o = json::object();
        o.obj["metric_key"] = json::string(def.key);
        o.obj["display_name"] = json::string(def.display_name);
        o.obj["formula"] = json::string(def.formula);
        o.obj["unit"] = json::string(def.kind);
        o.obj["precision"] = json::number(def.precision);
        o.obj["period_mode"] = json::string(def.period_mode);
        o.obj["op"] = json::string(def.op);
        json::Value req = json::array();
        for (const auto& k : def.required_inputs) req.arr.push_back(json::string(k));
        o.obj["required_inputs"] = req;
        arr.arr.push_back(o);
    }
    return json::stringify(arr);
}

// `--registry-fyjc`: Sprint 15D FYJC commercial-arithmetic metadata as JSON.
static std::string fyjc_registry_json() {
    json::Value arr = json::array();
    for (const auto& def : fyjc_registry()) {
        json::Value o = json::object();
        o.obj["metric_key"] = json::string(def.key);
        o.obj["display_name"] = json::string(def.display_name);
        o.obj["formula"] = json::string(def.formula);
        o.obj["unit"] = json::string(def.kind);
        o.obj["precision"] = json::number(def.precision);
        o.obj["period_mode"] = json::string(def.period_mode);
        o.obj["op"] = json::string(def.op);
        json::Value req = json::array();
        for (const auto& k : def.required_inputs) req.arr.push_back(json::string(k));
        o.obj["required_inputs"] = req;
        arr.arr.push_back(o);
    }
    return json::stringify(arr);
}

// `--selftest`: built-in deterministic assertions; exit code 0 on success.
static int selftest() {
    auto facts_of = [](const std::map<std::string, long double>& values) {
        std::map<std::string, Fact> out;
        for (const auto& kv : values) {
            Fact f;
            f.metric = kv.first;
            f.value = kv.second;
            f.has_value = true;
            f.reporting_period = "FY2025";
            f.provenance_tier = "DOCUMENT";
            out[kv.first] = f;
        }
        return out;
    };
    int failures = 0;
    auto expect = [&failures](bool ok, const std::string& label) {
        if (!ok) {
            std::cerr << "SELFTEST FAIL: " << label << "\n";
            ++failures;
        }
    };

    std::map<std::string, Fact> base = facts_of({
        {"Revenue", 281700000000.0L}, {"Net Profit", 98300000000.0L},
        {"Operating Profit", 125500000000.0L}, {"Equity", 268500000000.0L},
        {"Assets", 512200000000.0L}, {"Debt", 96600000000.0L},
        {"Current Assets", 21500000000.0L}, {"Current Liabilities", 15400000000.0L},
        {"EPS", 13.05L}, {"Previous EPS", 11.79L},
        {"Previous Revenue", 245100000000.0L},
    });

    expect(calculate_metric("ROE", base).display_value == "36.61%", "ROE 36.61%");
    expect(calculate_metric("ROA", base).display_value == "19.19%", "ROA 19.19%");
    expect(calculate_metric("Profit Margin", base).display_value == "34.90%", "Profit Margin");
    expect(calculate_metric("Operating Margin", base).display_value == "44.55%", "Operating Margin");
    expect(calculate_metric("Current Ratio", base).display_value == "1.40", "Current Ratio");
    expect(calculate_metric("Debt to Equity", base).display_value == "0.36", "Debt/Equity");
    base["Previous Revenue"].reporting_period = "FY2024";
    base["Previous EPS"].reporting_period = "FY2024";
    expect(calculate_metric("Revenue Growth", base).display_value == "14.93%", "Revenue Growth");
    expect(calculate_metric("EPS Growth", base).display_value == "10.69%", "EPS Growth");

    std::map<std::string, Fact> cagr = facts_of({
        {"CAGR Beginning Value", 200000000000.0L}, {"CAGR Ending Value", 281700000000.0L},
    });
    cagr["CAGR Beginning Value"].reporting_period = "FY2023";
    cagr["CAGR Ending Value"].reporting_period = "FY2025";
    expect(calculate_metric("CAGR", cagr).display_value == "18.68%", "CAGR 18.68%");

    std::map<std::string, Fact> no_equity = base;
    no_equity.erase("Equity");
    expect(calculate_metric("ROE", no_equity).status == Status::BLOCKED, "missing -> BLOCKED");

    std::map<std::string, Fact> zero_eq = base;
    zero_eq["Equity"].value = 0.0L;
    expect(calculate_metric("ROE", zero_eq).status == Status::BLOCKED, "zero denom -> BLOCKED");

    expect(calculate_metric("DCF", base).status == Status::UNANALYZED, "unknown -> UNANALYZED");

    // ---- Sprint 12A — extended registry + reverse solving (additive) ----
    std::map<std::string, Fact> pnl = facts_of({
        {"Revenue", 1000.0L}, {"Expenses", 800.0L},
        {"Profit", 200.0L}, {"Loss", 0.0L},
    });
    expect(calculate_metric("PROFIT", pnl).display_value == "200.00", "PROFIT forward");
    expect(solve_metric("PROFIT", "Expenses", pnl).display_value == "800.00", "PROFIT reverse Expenses");
    expect(solve_metric("PROFIT", "Revenue", pnl).display_value == "1000.00", "PROFIT reverse Revenue");

    std::map<std::string, Fact> lossf = facts_of({
        {"Revenue", 1000.0L}, {"Expenses", 1200.0L}, {"Loss", 200.0L},
    });
    expect(calculate_metric("LOSS", lossf).display_value == "200.00", "LOSS forward");
    expect(solve_metric("LOSS", "Expenses", lossf).display_value == "1200.00", "LOSS reverse Expenses (Revenue + Loss)");
    expect(solve_metric("LOSS", "Revenue", lossf).display_value == "1000.00", "LOSS reverse Revenue");

    std::map<std::string, Fact> at = facts_of({
        {"Revenue", 1000.0L}, {"Assets", 2000.0L}, {"Asset Turnover", 0.5L},
    });
    expect(calculate_metric("ASSET_TURNOVER", at).display_value == "0.50", "Asset Turnover forward");
    expect(solve_metric("ASSET_TURNOVER", "Revenue", at).display_value == "1000.00", "AT reverse Revenue");
    expect(solve_metric("ASSET_TURNOVER", "Assets", at).display_value == "2000.00", "AT reverse Assets");

    std::map<std::string, Fact> em = facts_of({
        {"Assets", 2000.0L}, {"Equity", 500.0L}, {"Equity Multiplier", 4.0L},
    });
    expect(calculate_metric("EQUITY_MULTIPLIER", em).display_value == "4.00", "Equity Multiplier forward");

    std::map<std::string, Fact> pm = facts_of({
        {"Profit", 200.0L}, {"Revenue", 1000.0L}, {"Profit Margin", 20.0L},
    });
    expect(calculate_metric("PROFIT_MARGIN", pm).display_value == "20.00%", "Profit Margin forward");
    expect(solve_metric("PROFIT_MARGIN", "Profit", pm).display_value == "200.00", "PM reverse Profit");
    expect(solve_metric("PROFIT_MARGIN", "Revenue", pm).display_value == "1000.00", "PM reverse Revenue");

    std::map<std::string, Fact> zero_assets = facts_of({
        {"Revenue", 1000.0L}, {"Assets", 0.0L},
    });
    expect(calculate_metric("ASSET_TURNOVER", zero_assets).status == Status::BLOCKED,
            "extended zero denom -> BLOCKED");
    expect(solve_metric("PROFIT", "DCF", pnl).status == Status::BLOCKED,
            "solve for non-variable -> BLOCKED");

    // ---- Sprint 12F — extended production coverage (additive) ----
    std::map<std::string, Fact> epsf = facts_of({
        {"Net Profit", 200.0L}, {"Shares Outstanding", 100.0L}, {"EPS", 2.0L},
    });
    expect(calculate_metric("EPS", epsf).display_value == "2.00", "EPS forward");
    expect(solve_metric("EPS", "Net Profit", epsf).display_value == "200.00",
            "EPS reverse Net Profit");
    expect(solve_metric("EPS", "Shares Outstanding", epsf).display_value == "100.00",
            "EPS reverse Shares");

    std::map<std::string, Fact> nmf = facts_of({
        {"Net Profit", 200.0L}, {"Revenue", 1000.0L}, {"Net Margin", 20.0L},
    });
    expect(calculate_metric("NET_MARGIN", nmf).display_value == "20.00%",
            "Net Margin forward");
    expect(solve_metric("NET_MARGIN", "Net Profit", nmf).display_value == "200.00",
            "Net Margin reverse Profit");
    expect(solve_metric("NET_MARGIN", "Revenue", nmf).display_value == "1000.00",
            "Net Margin reverse Revenue");

    // ---- Sprint 15D — FYJC commercial arithmetic (additive) ----
    std::map<std::string, Fact> comm = facts_of({
        {"Sales", 10000.0L}, {"Commission Rate", 5.0L}, {"Commission", 500.0L},
    });
    expect(calculate_metric("COMMISSION", comm).display_value == "500.00",
            "Commission forward");
    expect(solve_metric("COMMISSION", "Sales", comm).display_value == "10000.00",
            "Commission reverse Sales");
    expect(solve_metric("COMMISSION", "Commission Rate", comm).display_value
           == "5.00", "Commission reverse Rate");
    expect(solve_metric("COMMISSION", "List Price", comm).status
           == Status::BLOCKED, "Commission solve non-variable -> BLOCKED");

    std::map<std::string, Fact> spf = facts_of({
        {"Cost Price", 8000.0L}, {"Profit", 2000.0L},
        {"Selling Price", 10000.0L},
    });
    expect(calculate_metric("SELLING_PRICE", spf).display_value == "10000.00",
            "Selling Price forward");
    expect(solve_metric("SELLING_PRICE", "Profit", spf).display_value
           == "2000.00", "SP reverse Profit");
    expect(solve_metric("SELLING_PRICE", "Cost Price", spf).display_value
           == "8000.00", "SP reverse Cost Price");

    std::map<std::string, Fact> ppf = facts_of({
        {"Profit", 2000.0L}, {"Cost Price", 8000.0L},
        {"Profit Percent", 25.0L},
    });
    expect(calculate_metric("PROFIT_PERCENT", ppf).display_value == "25.00%",
            "Profit Percent forward");
    expect(solve_metric("PROFIT_PERCENT", "Profit", ppf).display_value
           == "2000.00", "PP reverse Profit");
    expect(solve_metric("PROFIT_PERCENT", "Cost Price", ppf).display_value
           == "8000.00", "PP reverse Cost Price");

    std::map<std::string, Fact> cbf = facts_of({
        {"Net Purchase", 9000.0L}, {"Amount Paid", 4410.0L},
        {"Creditor Balance", 4590.0L},
    });
    expect(calculate_metric("CREDITOR_BALANCE", cbf).display_value
           == "4590.00", "Creditor Balance forward");
    expect(solve_metric("CREDITOR_BALANCE", "Amount Paid", cbf).display_value
           == "4410.00", "CB reverse Amount Paid");

    std::map<std::string, Fact> tdf = facts_of({
        {"List Price", 10000.0L}, {"Trade Discount Rate", 10.0L},
        {"Trade Discount", 1000.0L},
    });
    expect(calculate_metric("TRADE_DISCOUNT", tdf).display_value == "1000.00",
            "Trade Discount forward");
    expect(solve_metric("TRADE_DISCOUNT", "List Price", tdf).display_value
           == "10000.00", "TD reverse List Price");
    expect(solve_metric("TRADE_DISCOUNT", "Trade Discount Rate", tdf)
           .display_value == "10.00", "TD reverse Rate");

    std::map<std::string, Fact> cdf = facts_of({
        {"Paid Amount", 4500.0L}, {"Cash Discount Rate", 2.0L},
        {"Cash Discount", 90.0L},
    });
    expect(calculate_metric("CASH_DISCOUNT", cdf).display_value == "90.00",
            "Cash Discount forward");
    expect(solve_metric("CASH_DISCOUNT", "Cash Discount Rate", cdf)
           .display_value == "2.00", "CD reverse Rate");

    std::map<std::string, Fact> qrf = facts_of({
        {"Current Assets", 500.0L}, {"Inventory", 100.0L},
        {"Current Liabilities", 200.0L}, {"Quick Ratio", 2.0L},
    });
    expect(calculate_metric("QUICK_RATIO", qrf).display_value == "2.00",
            "Quick Ratio forward");
    expect(solve_metric("QUICK_RATIO", "Current Assets", qrf).display_value == "500.00",
            "QR reverse Current Assets");
    expect(solve_metric("QUICK_RATIO", "Current Liabilities", qrf).display_value == "200.00",
            "QR reverse Current Liabilities");
    expect(solve_metric("QUICK_RATIO", "Inventory", qrf).status == Status::BLOCKED,
            "QR reverse Inventory -> BLOCKED (not registered)");

    std::map<std::string, Fact> dupf = facts_of({
        {"Net Profit", 200.0L}, {"Revenue", 1000.0L}, {"Total Assets", 2000.0L},
        {"Equity", 1000.0L},
    });
    expect(calculate_metric("DUPONT_PROFIT_MARGIN", dupf).display_value == "0.20",
            "DuPont Profit Margin (fraction)");
    expect(calculate_metric("DUPONT_ASSET_TURNOVER", dupf).display_value == "0.50",
            "DuPont Asset Turnover");
    expect(calculate_metric("DUPONT_EQUITY_MULTIPLIER", dupf).display_value == "2.00",
            "DuPont Equity Multiplier");
    std::map<std::string, Fact> duproe = facts_of({
        {"Profit Margin", 0.2L}, {"Asset Turnover", 0.5L},
        {"Equity Multiplier", 2.0L},
    });
    expect(calculate_metric("DUPONT_ROE", duproe).display_value == "20.00%",
            "DuPont ROE = PM x AT x EM");
    expect(solve_metric("DUPONT_ROE", "Profit Margin", duproe).status == Status::BLOCKED,
            "DuPont ROE reverse -> BLOCKED (no registered inverse)");

    std::map<std::string, Fact> roa_ta = facts_of({
        {"Net Profit", 200.0L}, {"Total Assets", 1000.0L},
    });
    expect(calculate_metric("ROA_TOTAL_ASSETS", roa_ta).display_value == "20.00%",
            "ROA (Total Assets vocabulary)");

    std::map<std::string, Fact> da = facts_of({
        {"Debt", 500.0L}, {"Total Assets", 2000.0L}, {"Debt to Assets", 0.25L},
    });
    expect(calculate_metric("DEBT_TO_ASSETS", da).display_value == "0.25",
            "Debt to Assets forward");
    expect(solve_metric("DEBT_TO_ASSETS", "Debt", da).display_value == "500.00",
            "Debt to Assets reverse Debt");

    std::map<std::string, Fact> ic = facts_of({
        {"Operating Profit", 300.0L}, {"Interest Expense", 100.0L},
    });
    expect(calculate_metric("INTEREST_COVERAGE", ic).display_value == "3.00",
            "Interest Coverage forward");

    std::map<std::string, Fact> oppf = facts_of({
        {"Revenue", 1000.0L}, {"Loss", 200.0L}, {"Profit", -200.0L},
    });
    expect(calculate_metric("PROFIT_LOSS_OPPOSITE", oppf).display_value == "-200.00",
            "Profit = -Loss");
    expect(solve_metric("PROFIT_LOSS_OPPOSITE", "Loss", oppf).display_value == "200.00",
            "Loss = -Profit (reverse)");

    std::map<std::string, Fact> cagr_fact_span = facts_of({
        {"CAGR Beginning Value", 100.0L}, {"CAGR Ending Value", 121.0L},
        {"CAGR Span Years", 2.0L},
    });
    expect(calculate_metric("CAGR", cagr_fact_span).display_value == "10.00%",
            "CAGR explicit span fact");

    if (failures == 0) {
        std::cout << "SELFTEST: ALL OK\n";
    } else {
        std::cout << "SELFTEST: " << failures << " FAILURE(S)\n";
    }
    return failures == 0 ? 0 : 1;
}

}  // namespace fte

int main(int argc, char** argv) {
    if (argc > 1 && std::strcmp(argv[1], "--worker") == 0) {
        // Sprint 15G - persistent worker mode (HFT-style authority transport).
        // Reads ONE JSON document per line on stdin and writes ONE JSON result
        // per line on stdout. Every document is executed through the exact same
        // run_cli() path as the one-shot CLI, so results are byte-identical to
        // spawning the binary once per request - only the process/JSON-bootstrap
        // overhead is removed. No persistent state is kept between documents:
        // each line is a fully independent deterministic execution.
        std::string line;
        while (std::getline(std::cin, line)) {
            if (line.empty() || line == "\r") continue;
            std::cout << fte::run_cli(line) << "\n";
            std::cout.flush();
        }
        return 0;
    }
    if (argc > 1 && std::strcmp(argv[1], "--registry") == 0) {
        std::cout << fte::registry_json() << "\n";
        return 0;
    }
    if (argc > 1 && std::strcmp(argv[1], "--registry-ext") == 0) {
        std::cout << fte::extended_registry_json() << "\n";
        return 0;
    }
    if (argc > 1 && std::strcmp(argv[1], "--registry-fyjc") == 0) {
        std::cout << fte::fyjc_registry_json() << "\n";
        return 0;
    }
    if (argc > 1 && std::strcmp(argv[1], "--selftest") == 0) {
        return fte::selftest();
    }
    if (argc > 1 && std::strcmp(argv[1], "--version") == 0) {
        std::cout << "fte-formula-engine 1.0.0 (Sprint 7 C++ deterministic engine + Sprint 12A extended registry)\n";
        return 0;
    }
    // Default: read one JSON document from stdin, write one to stdout.
    std::ostringstream buf;
    buf << std::cin.rdbuf();
    std::cout << fte::run_cli(buf.str()) << "\n";
    return 0;
}
