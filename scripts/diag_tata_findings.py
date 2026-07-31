"""Diagnostic: confirm root causes of the Tata Motors 20-F stress-test findings."""

import sys
import os
import re
from collections import Counter

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import logging
logging.basicConfig(level=logging.CRITICAL)
import warnings
warnings.filterwarnings("ignore")

from ingestion.extraction import extract_document


class _FileLike:
    def __init__(self, path):
        self.name = os.path.basename(path)
        with open(path, "rb") as f:
            self._data = f.read()
        self._pos = 0

    def read(self, *args):
        if args:
            n = args[0]
            out = self._data[self._pos:self._pos + n]
            self._pos += len(out)
            return out
        out = self._data[self._pos:]
        self._pos = len(self._data)
        return out

    def seek(self, offset, whence=0):
        if whence == 0:
            self._pos = offset
        elif whence == 1:
            self._pos += offset
        elif whence == 2:
            self._pos = len(self._data) + offset
        return self._pos


DOC = os.path.join(os.path.dirname(__file__), "..", "tests", "test_data", "tata_motors_20f.html")

r = extract_document(_FileLike(DOC))
parsed = r["parsed_document"]
facts = r["financial_facts"]
xbrl = parsed.get("xbrl_facts") or []
text = parsed.get("text", "")

print("=== 1. XBRL concept namespace breakdown (raw parser facts) ===")
pref = Counter()
for x in xbrl:
    c = x.get("concept", "")
    p = c.split(":")[0] if ":" in c else "?"
    pref[p] += 1
kept_names = set()
for f in facts:
    if f.get("source_type") == "XBRL":
        kept_names.add(f.get("metric_id"))
print("  raw XBRL facts by prefix:", dict(pref))
print("  XBRL facts kept by V2:", len([f for f in facts if f.get('source_type') == 'XBRL']),
      "| metric_ids:", sorted(kept_names)[:20])

print("\n=== 2. Indian digit grouping in text (3-2-2 grouping) ===")
indian = re.findall(r"\b\d{1,2},\d{2},\d{3}\b", text)
print("  count of x,xx,xxx patterns:", len(indian), "| samples:", indian[:6])
western = re.findall(r"\b\d{1,3},\d{3}\b", text)
print("  count of xxx,xxx patterns:", len(western))

print("\n=== 3. EBITDA mentions in text ===")
m = re.findall(r"[Ee]bitda", text)
print("  EBITDA mentions:", len(m))
shown = 0
for mm in re.finditer(r".{0,70}[Ee]bitda.{0,50}", text):
    print("   ", repr(mm.group(0)))
    shown += 1
    if shown >= 5:
        break

print("\n=== 4. EBITDA/EBIT facts in extraction ===")
hits = [f for f in facts if f["metric_id"] in ("EBITDA", "EBIT")]
print("  EBITDA/EBIT facts:", len(hits))
for f in hits[:6]:
    print("   ", f["metric_id"], f["metric_value"], f.get("fiscal_period"), f["source_type"])

print("\n=== 5. scale caption presence ===")
print('  "(in millions)" count:', text.count("(in millions)") + text.count("in millions"))
print('  "crore" count:', text.count("crore"))
print('  "lakh" count:', text.count("lakh"))

print("\n=== 6. facts with glossary-era periods ===")
bad_periods = [f for f in facts if f.get("fiscal_period") in (
    "1978", "1986", "FY2012", "FY2013", "FY2015", "FY 2018", "FY 2020")]
print("  facts with glossary-era periods:", len(bad_periods))
for f in bad_periods[:8]:
    print("   ", f["metric_id"], f["metric_value"], f.get("fiscal_period"),
          "| anchor:", str(f.get("evidence_text_anchor"))[:60])

print("\n=== 7. sample table-scale facts (crore-denominated, unscaled) ===")
cands = [f for f in facts if f.get("scale") and f["metric_id"] == "Revenue"]
print("  Revenue facts with scale metadata:", len(cands))
for f in cands[:6]:
    print("   ", f["metric_value"], "scale=", f.get("scale"), "norm=", f.get("normalized_value"),
          "| period=", f.get("fiscal_period"), "| src=", f["source_type"])
