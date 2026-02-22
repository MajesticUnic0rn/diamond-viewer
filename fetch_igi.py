"""
IGI Page Fetcher

Fetches the IGI verification page, extracts the embedded JSON blob,
downloads the PDF report, and saves raw HTML for offline inspection.
Tries requests first; falls back to Playwright to bypass Cloudflare.

Usage:
    uv run python fetch_igi.py LG689561771
    uv run python fetch_igi.py LG689561771 --with-pdf
    uv run python fetch_igi.py LG689561771 --with-pdf --print-html
    uv run python fetch_igi.py LG689561771 --browser          # skip requests, go straight to Playwright
    uv run python fetch_igi.py LG689561771 --headless          # Playwright in headless mode
"""
import sys
import os
import json
import re
import argparse
from pathlib import Path
from urllib.parse import quote

import requests
from playwright.sync_api import sync_playwright, Browser, Page


def parse_args():
    parser = argparse.ArgumentParser(description="Fetch IGI report page")
    parser.add_argument("report_number", help="IGI report number (e.g. LG689561771)")
    parser.add_argument("--print-html", action="store_true",
                        help="Print the first 2000 chars of HTML to stdout")
    parser.add_argument("--with-pdf", action="store_true",
                        help="Download the PDF report")
    parser.add_argument("--browser", action="store_true",
                        help="Skip requests and use Playwright directly")
    parser.add_argument("--headless", action="store_true",
                        help="Run Playwright in headless mode")
    parser.add_argument("--output", "-o", type=str, default=None,
                        help="Output JSON path (default: reports/{num}/{num}.json)")
    return parser.parse_args()


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;"
        "q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer": "https://www.igi.org/",
    "DNT": "1",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-User": "?1",
    "Cache-Control": "max-age=0",
}


def fetch_with_requests(report_number: str) -> str | None:
    """Try fetching with requests. Returns HTML on success, None on 403."""
    url = f"https://www.igi.org/Verify-Your-Report/?r={report_number}"

    session = requests.Session()
    print("[requests] Visiting igi.org homepage for cookies...")
    home_resp = session.get("https://www.igi.org/", headers=HEADERS, timeout=30)
    print(f"           Homepage: {home_resp.status_code}")

    if home_resp.status_code == 403:
        print("           Blocked by Cloudflare on homepage.")
        return None

    print(f"[requests] Fetching {url}")
    resp = session.get(url, headers=HEADERS, timeout=30)
    print(f"           Status: {resp.status_code}  Length: {len(resp.text)} chars")

    if resp.status_code == 403:
        print("           Blocked by Cloudflare.")
        return None

    resp.raise_for_status()
    return resp.text


def fetch_with_playwright(report_number: str, headless: bool = False,
                          with_pdf: bool = False, out_dir: Path | None = None) -> tuple[str, str | None]:
    """
    Fetch with a real browser via Playwright to bypass Cloudflare.
    Returns (html, pdf_path_or_None).
    """
    url = f"https://www.igi.org/Verify-Your-Report/?r={report_number}"
    pdf_path = None

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        ctx = browser.new_context(
            viewport={"width": 1440, "height": 1000},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        )
        page = ctx.new_page()

        print(f"[playwright] Loading {url}")
        page.goto(url, wait_until="domcontentloaded", timeout=60000)

        # Wait for Cloudflare challenge to resolve and page to render
        print("[playwright] Waiting for page to settle...")
        page.wait_for_timeout(5000)

        html = page.content()
        print(f"[playwright] Got {len(html)} chars of HTML")

        if with_pdf and out_dir:
            pdf_path = download_pdf(page, report_number, html, out_dir)

        browser.close()

    return html, pdf_path


def extract_json_string(html: str) -> str | None:
    """Extract the raw JSON string from the String.raw`` in the page."""
    match = re.search(r'String\.raw`(\[.*?\])`', html, re.DOTALL)
    if match:
        return match.group(1)
    return None


def download_pdf(page: Page, report_number: str, html: str, out_dir: Path) -> str | None:
    """
    Download the PDF by calling IGI's internal API endpoint from within
    the browser context (to reuse Cloudflare-cleared cookies).

    The page JS does:
      fetch(host + "API-IGI/viewpdf-url-v2.php?r=" + rn + "&url&json=" + json, {
          headers: { 'Ocp-Apim-Subscription-Key': '359cede0ba2c481ab32890967c95847a' }
      })
    which returns the actual PDF URL as plain text.
    """
    print("[pdf] Resolving PDF URL via IGI API...")

    # Get the raw JSON string that the page embeds
    json_string = extract_json_string(html)
    if not json_string:
        print("[pdf] Could not find JSON blob in HTML — skipping PDF.")
        return None

    # Use the browser to call the API (inherits Cloudflare cookies)
    api_key = os.getenv('IGI_API_KEY', '')
    if not api_key:
        print("[pdf] IGI_API_KEY environment variable not set — skipping PDF.")
        return None
    try:
        pdf_url = page.evaluate("""(args) => {
            const [rn, jsonStr, apiKey] = args;
            const apiUrl = "https://www.igi.org/API-IGI/viewpdf-url-v2.php?r=" + rn + "&url&json=" + encodeURIComponent(jsonStr);
            return fetch(apiUrl, {
                headers: {
                    'Ocp-Apim-Subscription-Key': apiKey,
                    'Content-type': 'text/plain'
                }
            }).then(r => r.text());
        }""", [report_number, json_string, api_key])
    except Exception as e:
        print(f"[pdf] API call failed: {e}")
        return None

    pdf_url = pdf_url.strip()
    print(f"[pdf] Got URL: {pdf_url}")

    if not pdf_url or pdf_url == "NO-PDF":
        print("[pdf] No PDF available for this report.")
        return None

    # Check if the response is an image rather than a PDF
    if re.search(r'\.(jpg|jpeg|png|gif)$', pdf_url, re.IGNORECASE):
        print(f"[pdf] URL points to an image, not a PDF. Downloading anyway...")

    # Download the actual PDF file via the browser
    try:
        resp = page.request.get(pdf_url)
        if resp.ok:
            pdf_dest = out_dir / f"{report_number}.pdf"
            pdf_dest.write_bytes(resp.body())
            print(f"[pdf] Saved ({len(resp.body())} bytes) to {pdf_dest}")
            return str(pdf_dest)
        else:
            print(f"[pdf] Download failed: HTTP {resp.status}")
            return None
    except Exception as e:
        print(f"[pdf] Download failed: {e}")
        return None


def extract_json_blob(html: str, report_number: str) -> dict | None:
    """
    Extract the embedded report JSON from the page HTML.
    Mirrors the extraction logic in scrape_igi.py.
    """
    # Strategy 1: Find String.raw`[{...}]` pattern
    match = re.search(r'String\.raw`(\[.*?\])`', html, re.DOTALL)
    if match:
        raw_json = match.group(1)
        try:
            data_list = json.loads(raw_json)
            if data_list and isinstance(data_list, list):
                return data_list[0]
        except json.JSONDecodeError:
            pass

    # Strategy 2: Find JSON object containing the report number
    pattern = r'\{[^{}]*"REPORT NUMBER"\s*:\s*"' + re.escape(report_number) + r'"[^}]*\}'
    match = re.search(pattern, html)
    if match:
        raw = match.group(0)
        raw = raw.replace("&quot;", '"').replace("&amp;", "&").replace("&#039;", "'")
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass

    # Strategy 3: HTML-encoded form
    match = re.search(
        r'&quot;REPORT NUMBER&quot;\s*:\s*&quot;' + re.escape(report_number) + r'&quot;',
        html,
    )
    if match:
        start = html.rfind("{", 0, match.start())
        end = html.find("}", match.end())
        if start >= 0 and end >= 0:
            raw = html[start:end + 1]
            raw = raw.replace("&quot;", '"').replace("&amp;", "&").replace("&#039;", "'")
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                pass

    return None


def main():
    args = parse_args()
    report_number = args.report_number

    out_dir = Path("reports") / report_number
    out_dir.mkdir(parents=True, exist_ok=True)

    # Fetch HTML
    html = None
    pdf_path = None
    used_playwright = False

    if not args.browser:
        try:
            html = fetch_with_requests(report_number)
        except requests.RequestException as e:
            print(f"[requests] Error: {e}")

    if html is None:
        if not args.browser:
            print("\n[fallback] Using Playwright to bypass Cloudflare...")
        html, pdf_path = fetch_with_playwright(
            report_number, headless=args.headless,
            with_pdf=args.with_pdf, out_dir=out_dir,
        )
        used_playwright = True

    # Save raw HTML
    html_path = out_dir / "page.html"
    html_path.write_text(html, encoding="utf-8")
    print(f"\nSaved HTML to {html_path}")

    # Optionally print a preview
    if args.print_html:
        preview = html[:2000].encode("utf-8", errors="replace").decode("utf-8")
        sys.stdout.buffer.write(b"\n--- HTML PREVIEW (first 2000 chars) ---\n")
        sys.stdout.buffer.write(preview.encode("utf-8"))
        sys.stdout.buffer.write(b"\n--- END PREVIEW ---\n\n")
        sys.stdout.buffer.flush()

    # Extract JSON
    print("Extracting embedded JSON data...")
    data = extract_json_blob(html, report_number)

    if data:
        print(f"Found report data with {len(data)} fields")
        json_path = args.output or str(out_dir / f"{report_number}.json")
        Path(json_path).parent.mkdir(parents=True, exist_ok=True)
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        print(f"Saved JSON to {json_path}")
    else:
        print("WARNING: No embedded JSON found in page.")
        print("The HTML has been saved — inspect it manually to see")
        print("if the page requires JavaScript rendering.")

    # PDF download if requests succeeded but we still need the PDF
    if args.with_pdf and not used_playwright:
        print("\n[pdf] PDF download requires Playwright (for Cloudflare cookies).")
        print("      Re-running with --browser to fetch PDF...")
        _, pdf_path = fetch_with_playwright(
            report_number, headless=args.headless,
            with_pdf=True, out_dir=out_dir,
        )

    if pdf_path:
        print(f"\nPDF saved to {pdf_path}")


if __name__ == "__main__":
    main()
