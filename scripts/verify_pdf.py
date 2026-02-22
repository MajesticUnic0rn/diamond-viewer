"""
Verify IGI PDF against JSON and extract clarity plot images.

Usage:
    uv run python verify_pdf.py LG689561771
"""
import sys
import json
import argparse
from pathlib import Path

import pdfplumber
import cv2
import numpy as np


def parse_args():
    parser = argparse.ArgumentParser(description="Verify IGI PDF vs JSON and extract images")
    parser.add_argument("report_number", help="IGI report number (e.g. LG689561771)")
    return parser.parse_args()


def extract_pdf_text(pdf_path: str) -> dict:
    """Extract all text from each page of the PDF."""
    pages = {}
    with pdfplumber.open(pdf_path) as pdf:
        print(f"PDF has {len(pdf.pages)} page(s)")
        for i, page in enumerate(pdf.pages):
            text = page.extract_text() or ""
            pages[i] = text
            print(f"\n--- Page {i + 1} ({page.width}x{page.height}) ---")
            print(text)
    return pages


def extract_images_from_pdf(pdf_path: str, out_dir: Path) -> list[Path]:
    """
    Render the PDF page at high res, then use pdfplumber's embedded image
    coordinates to crop each image object out of the rendered page.
    Returns list of saved image paths.
    """
    images_dir = out_dir / "images"
    images_dir.mkdir(exist_ok=True)
    saved = []
    resolution = 300

    with pdfplumber.open(pdf_path) as pdf:
        for page_num, page in enumerate(pdf.pages):
            # Render the full page
            page_img = page.to_image(resolution=resolution)
            page_path = images_dir / f"page_{page_num + 1}_full.png"
            page_img.save(str(page_path))
            saved.append(page_path)
            print(f"Saved full page render to {page_path}")

            # Scale factor: PDF points -> rendered pixels
            scale_x = resolution / 72.0
            scale_y = resolution / 72.0

            # Load rendered page with OpenCV for cropping
            rendered = cv2.imread(str(page_path))

            # Crop each embedded image using its PDF coordinates
            for img_idx, img_obj in enumerate(page.images):
                x0 = int(img_obj["x0"] * scale_x)
                y0 = int(img_obj["top"] * scale_y)
                x1 = int(img_obj["x1"] * scale_x)
                y1 = int(img_obj["bottom"] * scale_y)
                w = x1 - x0
                h = y1 - y0

                # Skip tiny images (icons, decorations)
                if w < 80 or h < 80:
                    continue

                # Crop with small padding
                pad = 5
                cy0 = max(0, y0 - pad)
                cx0 = max(0, x0 - pad)
                cy1 = min(rendered.shape[0], y1 + pad)
                cx1 = min(rendered.shape[1], x1 + pad)
                crop = rendered[cy0:cy1, cx0:cx1]

                crop_path = images_dir / f"page{page_num + 1}_img{img_idx:02d}_{w}x{h}.png"
                cv2.imwrite(str(crop_path), crop)
                saved.append(crop_path)
                print(f"  Cropped image {img_idx}: ({x0},{y0})-({x1},{y1}) "
                      f"size={w}x{h} -> {crop_path.name}")

    return saved


def extract_clarity_plots(pdf_path: str, out_dir: Path) -> list[Path]:
    """
    Extract the single clarity characteristic diamond diagram (table + pavilion
    side-by-side) from the PDF.

    Identification heuristics (all must pass):
    - Position below the "CLARITY CHARACTERISTICS" text
    - Aspect ratio between 1.5:1 and 2.5:1 (two diamonds side by side)
    - Minimum size: width > 120pt AND height > 60pt in PDF coordinates
    - Center-page X position (30%–75% of page width)
    - Returns only the single best match (largest area after filtering)
    """
    resolution = 300
    scale = resolution / 72.0
    plots = []

    with pdfplumber.open(pdf_path) as pdf:
        for page_num, page in enumerate(pdf.pages):
            page_w = page.width
            page_h = page.height

            # Find the "CLARITY CHARACTERISTICS" heading Y position
            words = page.extract_words()
            clarity_y = None
            for i, w in enumerate(words):
                if "CLARITY" in w["text"].upper():
                    # Check if next word is "CHARACTERISTICS"
                    if i + 1 < len(words) and "CHARACTERISTICS" in words[i + 1]["text"].upper():
                        clarity_y = w["top"]
                        break
                    # Or it might be a single combined word
                    if "CHARACTERISTICS" in w["text"].upper():
                        clarity_y = w["top"]
                        break

            if clarity_y is None:
                print("  No 'CLARITY CHARACTERISTICS' heading found, skipping page.")
                continue

            print(f"\n  Clarity section starts at y={clarity_y:.1f} "
                  f"(page height={page_h:.1f})")

            # Filter image objects with strict criteria
            candidates = []
            for img_obj in page.images:
                iw = img_obj["x1"] - img_obj["x0"]
                ih = img_obj["bottom"] - img_obj["top"]

                # Must be below clarity heading
                if img_obj["top"] < clarity_y - 20:
                    continue

                # Minimum size: width > 120pt AND height > 60pt
                if iw <= 120 or ih <= 60:
                    continue

                # Aspect ratio between 1.5:1 and 2.5:1
                aspect = iw / ih if ih > 0 else 0
                if aspect < 1.5 or aspect > 2.5:
                    continue

                # Center-page X position (30%–75% of page width)
                mid_x = (img_obj["x0"] + img_obj["x1"]) / 2
                if mid_x < page_w * 0.30 or mid_x > page_w * 0.75:
                    continue

                area = iw * ih
                candidates.append((area, img_obj))
                print(f"  Candidate: ({img_obj['x0']:.0f},{img_obj['top']:.0f})"
                      f"-({img_obj['x1']:.0f},{img_obj['bottom']:.0f}) "
                      f"w={iw:.0f} h={ih:.0f} aspect={aspect:.2f} area={area:.0f}")

            if not candidates:
                print("  No clarity plot candidates passed all filters.")
                continue

            # Pick the single best match (largest area)
            candidates.sort(key=lambda c: c[0], reverse=True)
            best = candidates[0][1]
            iw = best["x1"] - best["x0"]
            ih = best["bottom"] - best["top"]
            print(f"  Selected best: ({best['x0']:.0f},{best['top']:.0f})"
                  f"-({best['x1']:.0f},{best['bottom']:.0f}) "
                  f"w={iw:.0f} h={ih:.0f}")

            # Render page for cropping
            images_dir = out_dir / "images"
            images_dir.mkdir(exist_ok=True)
            rendered = cv2.imread(str(images_dir / f"page_{page_num + 1}_full.png"))
            if rendered is None:
                page_img = page.to_image(resolution=resolution)
                tmp = images_dir / f"page_{page_num + 1}_full.png"
                page_img.save(str(tmp))
                rendered = cv2.imread(str(tmp))

            x0 = int(best["x0"] * scale)
            y0 = int(best["top"] * scale)
            x1 = int(best["x1"] * scale)
            y1 = int(best["bottom"] * scale)
            w = x1 - x0
            h = y1 - y0

            pad = 10
            cy0 = max(0, y0 - pad)
            cx0 = max(0, x0 - pad)
            cy1 = min(rendered.shape[0], y1 + pad)
            cx1 = min(rendered.shape[1], x1 + pad)
            crop = rendered[cy0:cy1, cx0:cx1]

            # Save as single canonical output
            crop_path = out_dir / "clarity_plot.png"
            cv2.imwrite(str(crop_path), crop)
            plots.append(crop_path)
            print(f"  Saved clarity plot: {w}x{h}px -> {crop_path}")

    return plots


def compare_json_to_pdf(json_data: dict, pdf_text: str):
    """Compare key fields from JSON against text found in PDF."""
    print("\n" + "=" * 60)
    print("  JSON vs PDF Comparison")
    print("=" * 60)

    pdf_norm = pdf_text.upper().replace("\n", " ").replace("  ", " ")

    checks = [
        ("REPORT NUMBER", json_data.get("REPORT NUMBER", "")),
        ("REPORT DATE", json_data.get("REPORT DATE", "")),
        ("DESCRIPTION", json_data.get("DESCRIPTION", "")),
        ("SHAPE AND CUT", json_data.get("SHAPE AND CUT", "")),
        ("CARAT WEIGHT", json_data.get("CARAT WEIGHT", "")),
        ("COLOR GRADE", json_data.get("COLOR GRADE", "")),
        ("CLARITY GRADE", json_data.get("CLARITY GRADE", "")),
        ("POLISH", json_data.get("POLISH", "")),
        ("SYMMETRY", json_data.get("SYMMETRY", "")),
        ("FLUORESCENCE", json_data.get("FLUORESCENCE", "")),
        ("Measurements", json_data.get("Measurements", "")),
        ("Table Size", json_data.get("Table Size", "")),
        ("Total Depth", json_data.get("Total Depth", "")),
    ]

    matched = 0
    failed = 0
    for label, json_val in checks:
        if not json_val:
            print(f"  {label:20s}  [SKIP] empty in JSON")
            continue

        search_val = json_val.upper().strip()
        search_variants = [
            search_val,
            search_val.replace(" X ", " × "),
            search_val.replace("×", "X"),
        ]

        found = any(v in pdf_norm for v in search_variants)
        status = "OK" if found else "MISSING"
        if found:
            matched += 1
        else:
            failed += 1
        print(f"  {label:20s}  [{status:7s}]  {json_val}")

    print(f"\n  Result: {matched}/{matched + failed} fields matched in PDF text")
    print("=" * 60)


def main():
    args = parse_args()
    report_number = args.report_number

    out_dir = Path("public/data") / report_number
    pdf_path = out_dir / f"{report_number}.pdf"
    json_path = out_dir / f"{report_number}.json"

    if not pdf_path.exists():
        print(f"ERROR: PDF not found at {pdf_path}")
        sys.exit(1)
    if not json_path.exists():
        print(f"ERROR: JSON not found at {json_path}")
        sys.exit(1)

    with open(json_path, "r", encoding="utf-8") as f:
        json_data = json.load(f)

    # 1. Extract text and compare
    print("=" * 60)
    print("  PDF TEXT EXTRACTION")
    print("=" * 60)
    pages = extract_pdf_text(str(pdf_path))

    all_text = "\n".join(pages.values())
    compare_json_to_pdf(json_data, all_text)

    # 2. Extract all embedded images
    print("\n" + "=" * 60)
    print("  ALL EMBEDDED IMAGES")
    print("=" * 60)
    all_images = extract_images_from_pdf(str(pdf_path), out_dir)

    # 3. Extract clarity plots specifically
    print("\n" + "=" * 60)
    print("  CLARITY CHARACTERISTIC PLOTS")
    print("=" * 60)
    plots = extract_clarity_plots(str(pdf_path), out_dir)

    if plots:
        print(f"\nExtracted clarity plot: {plots[0]}")
    else:
        print("\nNo clarity plot extracted.")


if __name__ == "__main__":
    main()
