"""
Download a publicly accessible financial filing from SEC EDGAR.
SEC does not block automated access when a valid User-Agent is provided.
Downloads Apple's 10-K which is a large, complex financial document
with all the characteristics needed for Agentic RAG stress testing.
"""
import requests
import json
import os

session = requests.Session()
session.headers.update({
    'User-Agent': 'FinancialTimelineEngine/1.0 (research-stress-test@example.com)',
    'Accept': 'application/json, text/html, */*',
    'Accept-Encoding': 'gzip, deflate',
})

output_dir = os.path.join(os.path.dirname(__file__), '..', 'tests', 'test_data')
os.makedirs(output_dir, exist_ok=True)

# Get Apple's company submissions (CIK: 0000320193)
print("Fetching Apple company submissions from SEC EDGAR...")
try:
    resp = session.get(
        'https://data.sec.gov/submissions/CIK0000320193.json',
        timeout=30,
    )
    print(f"Submissions API: HTTP {resp.status_code}")
    if resp.status_code == 200:
        data = resp.json()
        filings = data.get('filings', {}).get('recent', {})
        forms = filings.get('form', [])
        for i, form in enumerate(forms):
            if form in ('10-K',):
                primary_doc = filings.get('primaryDocument', [])[i]
                acc_num = filings.get('accessionNumber', [])[i]
                filed_date = filings.get('filingDate', [])[i]
                doc_url = (
                    f'https://www.sec.gov/Archives/edgar/data/320193/'
                    f'{acc_num.replace("-", "")}/{primary_doc}'
                )
                print(f"\nLatest 10-K filing:")
                print(f"  Filing date: {filed_date}")
                print(f"  Accession: {acc_num}")
                print(f"  Document: {primary_doc}")
                print(f"  URL: {doc_url}")

                # Download the filing document
                print("\nDownloading...")
                doc_resp = session.get(doc_url, timeout=120, stream=True)
                ct = doc_resp.headers.get('content-type', '')
                content_length = doc_resp.headers.get('content-length', '?')
                print(f"HTTP {doc_resp.status_code}, Type: {ct}, Size header: {content_length}")

                if doc_resp.status_code == 200:
                    out_path = os.path.join(output_dir, 'apple_10k_2024.html')
                    with open(out_path, 'wb') as f:
                        for chunk in doc_resp.iter_content(chunk_size=8192):
                            if chunk:
                                f.write(chunk)
                    actual_size = os.path.getsize(out_path)
                    print(f"DOWNLOADED to {out_path}")
                    print(f"Actual size: {actual_size} bytes ({actual_size/1024/1024:.1f} MB)")

                    # Also try to get the PDF version if available
                    pdf_url = doc_url.rsplit('.', 1)[0] + '.pdf'
                    pdf_resp = session.get(pdf_url, timeout=60)
                    if pdf_resp.status_code == 200 and len(pdf_resp.content) > 100000:
                        pdf_path = os.path.join(output_dir, 'apple_10k_2024.pdf')
                        with open(pdf_path, 'wb') as f:
                            f.write(pdf_resp.content)
                        print(f"PDF DOWNLOADED to {pdf_path}")
                        print(f"PDF size: {len(pdf_resp.content)} bytes ({len(pdf_resp.content)/1024/1024:.1f} MB)")
                    else:
                        print(f"PDF not available (HTTP {pdf_resp.status_code})")
                break

except Exception as e:
    print(f"Error: {e}")

print("\nDone.")
