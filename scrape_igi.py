"""
IGI Diamond Report Scraper

Extracts diamond grading data from IGI's Verify Your Report page.
The page embeds a full JSON blob in a <script> tag containing all
report data including proportions. This script extracts that JSON,
downloads the clarity plot image, and optionally grabs the PDF.

Usage:
    uv run python scrape_igi.py LG689561771
    uv run python scrape_igi.py LG689561771 --headless
    uv run python scrape_igi.py LG689561771 --with-pdf --with-plot
"""
import sys
import json
import re
import argparse
from pathlib import Path
from playwright.sync_api import sync_playwright


def parse_args():
    parser = argparse.ArgumentParser(description="Scrape IGI diamond report data")
    parser.add_argument("report_number", help="IGI report number (e.g. LG689561771)")
    parser.add_argument("--headless", action="store_true", help="Run in headless mode")
    parser.add_argument("--with-pdf", action="store_true", help="Download the PDF report")
    parser.add_argument("--with-plot", action="store_true", help="Download the clarity plot image")
    parser.add_argument("--screenshot", action="store_true", help="Save a page screenshot")
    parser.add_argument("--output", "-o", type=str, default=None, help="Output JSON path")
    return parser.parse_args()


def scrape_report(report_number: str, headless: bool = False,
                  with_pdf: bool = False, with_plot: bool = False,
                  screenshot: bool = False) -> dict:
    url = f"https://www.igi.org/Verify-Your-Report/?r={report_number}"
    out_dir = Path("reports") / report_number
    out_dir.mkdir(parents=True, exist_ok=True)

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

        print(f"[1/5] Loading {url}")
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(5000)

        html = page.content()

        if screenshot:
            page.screenshot(path=str(out_dir / "page.png"), full_page=True)
            print("      Saved page screenshot")

        # ── Extract JSON blob from <script> ──
        print("[2/5] Extracting embedded JSON data...")
        report_data = extract_json_blob(html, report_number)

        if not report_data:
            print("ERROR: Could not extract report data from page.")
            browser.close()
            return {"error": "No data found", "report_number": report_number}

        # ── Capture proportion diagram screenshot ──
        print("[3/5] Capturing proportion diagram...")
        try:
            props_el = page.query_selector("#props, .proportions")
            if props_el and props_el.is_visible():
                props_el.screenshot(path=str(out_dir / "proportions.png"))
                report_data["_files"]["proportions_image"] = str(out_dir / "proportions.png")
                print("      Saved proportions diagram")
        except Exception as e:
            print(f"      Could not capture diagram: {e}")

        # ── Download clarity plot from S3 ──
        if with_plot:
            print("[4/5] Downloading clarity plot...")
            plot_url = f"https://s3.ap-south-1.amazonaws.com/igi-global-plot/{report_number}.jpg"
            try:
                resp = page.request.get(plot_url)
                if resp.ok:
                    plot_path = out_dir / "clarity_plot.jpg"
                    plot_path.write_bytes(resp.body())
                    report_data["_files"]["clarity_plot"] = str(plot_path)
                    print(f"      Saved clarity plot ({len(resp.body())} bytes)")
                else:
                    print(f"      Plot not available (HTTP {resp.status})")
            except Exception as e:
                print(f"      Could not download plot: {e}")
        else:
            print("[4/5] Skipping plot download (use --with-plot)")

        # ── Download PDF ──
        if with_pdf:
            print("[5/5] Downloading PDF report...")
            try:
                # The page fetches the PDF URL via JS; intercept the console log
                pdf_url = page.evaluate("""() => {
                    try {
                        const rn = document.querySelector('input[name="r"]')?.value
                            || new URLSearchParams(window.location.search).get('r');
                        // Try the direct ecert viewer URL
                        return "https://igionline.com/ecert/viewpdf.htm?itemno=" + rn + "&view=FitH";
                    } catch(e) { return null; }
                }""")
                if pdf_url:
                    report_data["_files"]["pdf_viewer_url"] = pdf_url
                    print(f"      PDF viewer URL: {pdf_url}")
            except Exception as e:
                print(f"      Could not get PDF URL: {e}")
        else:
            print("[5/5] Skipping PDF download (use --with-pdf)")

        browser.close()

    return report_data


def extract_json_blob(html: str, report_number: str) -> dict | None:
    """
    The IGI page embeds the full report data as a JSON string in a
    JavaScript variable like:
        var json = String.raw`[{...}]`
    or similar. We extract and parse that JSON.
    """
    # Strategy 1: Find String.raw`[{...}]` pattern
    match = re.search(r'String\.raw`(\[.*?\])`', html, re.DOTALL)
    if match:
        raw_json = match.group(1)
        try:
            data_list = json.loads(raw_json)
            if data_list and isinstance(data_list, list):
                return normalize_report(data_list[0], report_number)
        except json.JSONDecodeError:
            pass

    # Strategy 2: Find JSON containing the report number
    pattern = r'\{[^{}]*"REPORT NUMBER"\s*:\s*"' + re.escape(report_number) + r'"[^}]*\}'
    match = re.search(pattern, html)
    if match:
        raw = match.group(0)
        # Decode HTML entities
        raw = raw.replace("&quot;", '"').replace("&amp;", "&").replace("&#039;", "'")
        try:
            data = json.loads(raw)
            return normalize_report(data, report_number)
        except json.JSONDecodeError:
            pass

    # Strategy 3: Find it in HTML-encoded form
    match = re.search(
        r'&quot;REPORT NUMBER&quot;\s*:\s*&quot;' + re.escape(report_number) + r'&quot;',
        html
    )
    if match:
        start = html.rfind("{", 0, match.start())
        end = html.find("}", match.end())
        if start >= 0 and end >= 0:
            raw = html[start:end + 1]
            raw = raw.replace("&quot;", '"').replace("&amp;", "&").replace("&#039;", "'")
            try:
                data = json.loads(raw)
                return normalize_report(data, report_number)
            except json.JSONDecodeError:
                pass

    return None


def normalize_report(raw: dict, report_number: str) -> dict:
    """Convert the raw IGI JSON into a clean, structured format."""

    # Parse crown height + angle (format: "14.5% - 36deg")
    crown_raw = raw.get("Crown Height", "")
    crown_height_pct = None
    crown_angle_deg = None
    m = re.match(r"([\d.]+)%?\s*-?\s*([\d.]+)", crown_raw)
    if m:
        crown_height_pct = float(m.group(1))
        crown_angle_deg = float(m.group(2))

    # Parse pavilion depth + angle (format: "50% - 37.8deg")
    pav_raw = raw.get("Pavilion Depth", "")
    pavilion_depth_pct = None
    pavilion_angle_deg = None
    m = re.match(r"([\d.]+)%?\s*-?\s*([\d.]+)", pav_raw)
    if m:
        pavilion_depth_pct = float(m.group(1))
        pavilion_angle_deg = float(m.group(2))

    # Parse table size
    table_raw = raw.get("Table Size", "")
    table_pct = None
    m = re.match(r"([\d.]+)", table_raw)
    if m:
        table_pct = float(m.group(1))

    # Parse total depth
    depth_raw = raw.get("Total Depth", "")
    depth_pct = None
    m = re.match(r"([\d.]+)", depth_raw)
    if m:
        depth_pct = float(m.group(1))

    # Parse measurements
    meas = raw.get("Measurements", "")
    length_mm = width_mm = depth_mm = None
    m = re.match(r"([\d.]+)\s*x\s*([\d.]+)\s*x\s*([\d.]+)", meas)
    if m:
        length_mm = float(m.group(1))
        width_mm = float(m.group(2))
        depth_mm = float(m.group(3))

    # Parse carat
    carat_raw = raw.get("CARAT WEIGHT", "")
    carat = None
    m = re.match(r"([\d.]+)", carat_raw)
    if m:
        carat = float(m.group(1))

    # Determine origin
    desc = raw.get("DESCRIPTION", "").upper()
    origin = "Laboratory Grown" if "LABORATORY" in desc or "LAB" in desc else "Natural"

    # Growth method from comments
    comments = raw.get("COMMENTS", "")
    growth_method = "Unknown"
    if "HPHT" in comments.upper():
        growth_method = "HPHT"
    elif "CVD" in comments.upper():
        growth_method = "CVD"

    report = {
        "report_number": report_number,
        "source": "IGI",
        "url": f"https://www.igi.org/Verify-Your-Report/?r={report_number}",

        # Identity
        "issue_date": raw.get("REPORT DATE", ""),
        "description": raw.get("DESCRIPTION", ""),
        "origin": origin,
        "growth_method": growth_method,

        # Shape
        "shape": raw.get("SHAPE AND CUT", ""),

        # Measurements
        "measurements_mm": meas,
        "length_mm": length_mm,
        "width_mm": width_mm,
        "depth_mm": depth_mm,

        # 4Cs
        "carat": carat,
        "carat_weight": carat_raw,
        "color_grade": raw.get("COLOR GRADE", ""),
        "clarity_grade": raw.get("CLARITY GRADE", ""),
        "cut_grade": raw.get("CUT GRADE", "") or None,

        # Finish
        "polish": raw.get("POLISH", ""),
        "symmetry": raw.get("SYMMETRY", ""),
        "fluorescence": raw.get("FLUORESCENCE", ""),

        # Proportions
        "proportions": {
            "table_pct": table_pct,
            "total_depth_pct": depth_pct,
            "crown_height_pct": crown_height_pct,
            "crown_angle_deg": crown_angle_deg,
            "pavilion_depth_pct": pavilion_depth_pct,
            "pavilion_angle_deg": pavilion_angle_deg,
            "girdle": raw.get("Girdle Thickness", ""),
            "culet": raw.get("Culet", ""),
        },

        # Metadata
        "inscriptions": raw.get("Inscription(s)", ""),
        "comments": comments.strip(),
        "report_type": raw.get("REPORT_TYPE", ""),
        "pdf_filename": raw.get("REPORT1_PDF", ""),

        # Image URLs
        "plot_image_url": f"https://s3.ap-south-1.amazonaws.com/igi-global-plot/{report_number}.jpg",
        "pdf_viewer_url": f"https://igionline.com/ecert/viewpdf.htm?itemno={report_number}&view=FitH",

        # Raw data (preserved for debugging)
        "_raw": raw,
        "_files": {},
    }

    return report


def print_report(data: dict):
    """Pretty-print the report to terminal."""
    if "error" in data:
        print(f"\nERROR: {data['error']}")
        return

    print()
    print("=" * 62)
    print(f"  IGI REPORT: {data.get('report_number', '?')}")
    print(f"  {data.get('url', '')}")
    print("=" * 62)

    section = [
        ("IDENTITY", [
            ("Issue Date", data.get("issue_date")),
            ("Description", data.get("description")),
            ("Origin", data.get("origin")),
            ("Growth Method", data.get("growth_method")),
            ("Shape", data.get("shape")),
        ]),
        ("GRADING", [
            ("Measurements", data.get("measurements_mm")),
            ("Carat Weight", data.get("carat_weight")),
            ("Color", data.get("color_grade")),
            ("Clarity", data.get("clarity_grade")),
            ("Cut", data.get("cut_grade") or "(not graded)"),
            ("Polish", data.get("polish")),
            ("Symmetry", data.get("symmetry")),
            ("Fluorescence", data.get("fluorescence")),
        ]),
        ("PROPORTIONS", [
            ("Table", f"{p}%" if (p := data.get('proportions', {}).get('table_pct')) else "?"),
            ("Total Depth", f"{p}%" if (p := data.get('proportions', {}).get('total_depth_pct')) else "?"),
            ("Crown Height", f"{p}%" if (p := data.get('proportions', {}).get('crown_height_pct')) else "?"),
            ("Crown Angle", f"{p} deg" if (p := data.get('proportions', {}).get('crown_angle_deg')) else "?"),
            ("Pavilion Depth", f"{p}%" if (p := data.get('proportions', {}).get('pavilion_depth_pct')) else "?"),
            ("Pavilion Angle", f"{p} deg" if (p := data.get('proportions', {}).get('pavilion_angle_deg')) else "?"),
            ("Girdle", data.get("proportions", {}).get("girdle") or "?"),
            ("Culet", data.get("proportions", {}).get("culet") or "?"),
        ]),
    ]

    for section_name, fields in section:
        print(f"\n  {section_name}")
        print(f"  {'-' * 40}")
        for label, val in fields:
            if val:
                print(f"  {label:20s}  {val}")

    if data.get("comments"):
        print(f"\n  COMMENTS")
        print(f"  {'-' * 40}")
        for line in data["comments"].split("\n"):
            line = line.strip()
            if line:
                print(f"  {line}")

    if data.get("inscriptions"):
        print(f"\n  Inscription: {data['inscriptions']}")

    print(f"\n  Plot Image:  {data.get('plot_image_url', 'N/A')}")
    print(f"  PDF Viewer:  {data.get('pdf_viewer_url', 'N/A')}")
    print("=" * 62)
    print()


if __name__ == "__main__":
    args = parse_args()

    data = scrape_report(
        report_number=args.report_number,
        headless=args.headless,
        with_pdf=args.with_pdf,
        with_plot=args.with_plot,
        screenshot=args.screenshot,
    )

    print_report(data)

    # Save JSON
    out_path = args.output or f"reports/{args.report_number}/{args.report_number}.json"
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"Saved to {out_path}")
