"""
Find Tata Motors Limited's CIK on SEC EDGAR and download its latest 20-F.
Tata Motors ADR ticker: TTM on NYSE. Also searchable by company name.
"""

import requests
import re
import json
import os
import sys

session = requests.Session()
session.headers.update({
    'User-Agent': 'FinancialTimelineEngine/1.0 (research-stress-test@example.com)',
    'Accept-Encoding': 'gzip, deflate',
})


def find_cik_by_name(name_query: str) -> str:
    """Use EDGAR full-text company search API."""
    url = f'https://www.sec.gov/cgi-bin/browse-edgar'
    params = {
        'action': 'getcompany',
        'company': name_query,
        'type': '20-F',
        'dateb': '',
        'owner': 'include',
        'count': '10',
        'output': 'atom',
    }
    r = session.get(url, params=params, timeout=30)
    print(f"browse-edgar (name='{name_query}'): HTTP {r.status_code}")
    if r.status_code != 200:
        return ""
    text = r.text
    # Find CIK values
    ciks = re.findall(r'CIK=(\d{10})', text)
    names = re.findall(r'<title>(.*?)</title>', text)
    for i, cik in enumerate(dict.fromkeys(ciks)):
        nm = names[i + 1] if i + 1 < len(names) else ''
        print(f"  CIK {cik}  {nm}")
    return dict.fromkeys(ciks)[0] if ciks else ""


def find_cik_by_ticker(ticker: str) -> str:
    url = f'https://www.sec.gov/cgi-bin/browse-edgar'
    params = {
        'action': 'getcompany',
        'CIK': ticker,
        'type': '20-F',
        'dateb': '',
        'owner': 'include',
        'count': '5',
        'output': 'atom',
    }
    r = session.get(url, params=params, timeout=30)
    print(f"browse-edgar (ticker={ticker}): HTTP {r.status_code}")
    if r.status_code != 200:
        return ""
    m = re.search(r'CIK=(\d{10})', r.text)
    if m:
        print(f"  Found CIK: {m.group(1)}")
        return m.group(1)
    print("  No CIK in response")
    return ""


def download_latest_20f(cik: str, output_stem: str):
    url = f'https://data.sec.gov/submissions/CIK{int(cik):010d}.json'
    r = session.get(url, timeout=30)
    print(f"submissions: HTTP {r.status_code}")
    if r.status_code != 200:
        return False
    data = r.json()
    recent = data.get('filings', {}).get('recent', {})
    forms = recent.get('form', [])
    for i, form in enumerate(forms):
        if form in ('20-F', '20-F/A'):
            primary = recent.get('primaryDocument', [])[i]
            acc = recent.get('accessionNumber', [])[i]
            fdate = recent.get('filingDate', [])[i]
            base = f'https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc.replace("-", "")}'
            print(f"\nLatest {form} filed {fdate}: {primary}")
            # Try PDF
            pdf_url = f'{base}/{primary.rsplit(".", 1)[0]}.pdf'
            pr = session.get(pdf_url, timeout=120, stream=True)
            ct = pr.headers.get('content-type', '')
            print(f"  PDF {pr.status_code} ({ct})")
            if pr.status_code == 200 and ('pdf' in ct.lower() or pr.headers.get('content-length', '0') > '500000'):
                out = os.path.join(os.path.dirname(__file__), '..', 'tests', 'test_data', f'{output_stem}.pdf')
                with open(out, 'wb') as f:
                    for chunk in pr.iter_content(8192):
                        if chunk:
                            f.write(chunk)
                print(f"  SAVED PDF: {out} ({os.path.getsize(out)/1024/1024:.1f} MB)")
                return True
            # Fallback to primary doc (HTML)
            dr = session.get(f'{base}/{primary}', timeout=120, stream=True)
            if dr.status_code == 200:
                out = os.path.join(os.path.dirname(__file__), '..', 'tests', 'test_data', f'{output_stem}.html')
                with open(out, 'wb') as f:
                    for chunk in dr.iter_content(8192):
                        if chunk:
                            f.write(chunk)
                print(f"  SAVED HTML: {out} ({os.path.getsize(out)/1024/1024:.1f} MB)")
                return True
    return False


if __name__ == '__main__':
    os.makedirs(os.path.join(os.path.dirname(__file__), '..', 'tests', 'test_data'), exist_ok=True)
    cik = ""
    print("=== Step 1: find Tata Motors CIK ===")
    cik = find_cik_by_ticker('TTM')
    if not cik:
        cik = find_cik_by_name('Tata Motors')
    if not cik:
        print("Could not find CIK. Exiting.")
        sys.exit(1)
    print(f"\n=== Step 2: download 20-F for CIK {cik} ===")
    ok = download_latest_20f(cik, 'tata_motors_20f')
    if ok:
        print("\nDONE.")
    else:
        print("\nNo 20-F downloaded.")
        sys.exit(1)
