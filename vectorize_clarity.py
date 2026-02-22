"""
Vectorize a clarity plot image into structured JSON for Three.js rendering.

Takes the raster clarity plot (table + pavilion side-by-side) and extracts:
- Diamond outlines as polygons
- Facet lines as line segments
- Inclusion markers as circles with positions

All coordinates are normalized to 0.0–1.0 relative to each view's bounding box.

Usage:
    uv run python vectorize_clarity.py LG689561771
"""
import sys
import json
import argparse
from pathlib import Path

import cv2
import numpy as np


def parse_args():
    parser = argparse.ArgumentParser(description="Vectorize clarity plot to JSON")
    parser.add_argument("report_number", help="IGI report number (e.g. LG689561771)")
    return parser.parse_args()


def find_clarity_image(report_dir: Path) -> Path:
    """Find the clarity plot image, checking canonical path then fallback."""
    canonical = report_dir / "clarity_plot.png"
    if canonical.exists():
        return canonical
    fallback = report_dir / "images" / "clarity_plot_1.png"
    if fallback.exists():
        return fallback
    raise FileNotFoundError(
        f"No clarity plot found. Looked at:\n  {canonical}\n  {fallback}\n"
        "Run verify_pdf.py first to extract it."
    )


def split_views(img: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Split the image at the horizontal midpoint into table (left) and pavilion (right)."""
    h, w = img.shape[:2]
    mid = w // 2
    # Find the actual gap between the two diamonds by looking for a vertical
    # strip of mostly white pixels near the center
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    search_start = int(w * 0.35)
    search_end = int(w * 0.65)
    # Sum dark pixels per column in the search region
    _, bw = cv2.threshold(gray, 200, 255, cv2.THRESH_BINARY)
    col_dark = []
    for x in range(search_start, search_end):
        dark_count = np.count_nonzero(bw[:, x] == 0)
        col_dark.append((dark_count, x))
    # The gap column has the fewest dark pixels
    col_dark.sort()
    gap_x = col_dark[0][1]
    print(f"  Split gap detected at x={gap_x} (image width={w})")

    left = img[:, :gap_x]
    right = img[:, gap_x:]
    return left, right


def extract_outline(view: np.ndarray) -> list[list[float]]:
    """Extract the diamond outline as a normalized polygon."""
    gray = cv2.cvtColor(view, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape

    # Threshold to get black lines on white background, then invert
    _, binary = cv2.threshold(gray, 180, 255, cv2.THRESH_BINARY_INV)

    # Dilate slightly to connect any broken lines
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    binary = cv2.dilate(binary, kernel, iterations=1)

    # Find contours — the outline is the largest one
    contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return []

    largest = max(contours, key=cv2.contourArea)

    # Approximate to reduce points while keeping shape
    perimeter = cv2.arcLength(largest, True)
    epsilon = 0.008 * perimeter
    approx = cv2.approxPolyDP(largest, epsilon, True)

    # Normalize to 0–1
    points = []
    for pt in approx:
        px, py = pt[0]
        points.append([round(px / w, 4), round(py / h, 4)])

    return points


def extract_facet_lines(view: np.ndarray, outline_pts: list[list[float]]) -> list[list[list[float]]]:
    """Extract internal facet lines by skeletonizing the black lines and
    running Hough on the skeleton. This avoids the double-edge problem
    that Canny creates on thick drawn lines."""
    gray = cv2.cvtColor(view, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape

    # Create a mask from the outline to only look inside the diamond
    if outline_pts:
        outline_px = np.array(
            [[int(p[0] * w), int(p[1] * h)] for p in outline_pts], dtype=np.int32
        )
        mask = np.zeros((h, w), dtype=np.uint8)
        cv2.fillPoly(mask, [outline_px], 255)
        # Erode mask aggressively so we don't pick up the outline itself
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (13, 13))
        mask = cv2.erode(mask, kernel, iterations=1)
    else:
        mask = np.ones((h, w), dtype=np.uint8) * 255

    # Threshold to get black lines (inverted: lines are white)
    _, binary = cv2.threshold(gray, 160, 255, cv2.THRESH_BINARY_INV)
    binary = cv2.bitwise_and(binary, binary, mask=mask)

    # Skeletonize to single-pixel-wide lines
    skeleton = _skeletonize(binary)

    # Hough line detection on the skeleton
    lines = cv2.HoughLinesP(
        skeleton,
        rho=1,
        theta=np.pi / 180,
        threshold=30,
        minLineLength=max(25, int(min(w, h) * 0.08)),
        maxLineGap=4,
    )

    if lines is None:
        return []

    # Convert to normalized segments and merge nearby endpoints
    segments = []
    for line in lines:
        x1, y1, x2, y2 = line[0]
        seg = [
            [round(x1 / w, 4), round(y1 / h, 4)],
            [round(x2 / w, 4), round(y2 / h, 4)],
        ]
        segments.append(seg)

    segments = _merge_nearby_segments(segments)

    # Filter out segments that don't actually sit on black pixels in the original.
    # Must run AFTER merge since merging can shift endpoints off-ink.
    _, line_mask = cv2.threshold(gray, 160, 255, cv2.THRESH_BINARY_INV)
    segments = _filter_on_ink(segments, line_mask, w, h, coverage_thresh=0.55)

    return segments


def _filter_on_ink(
    segments: list, line_mask: np.ndarray, w: int, h: int, coverage_thresh: float = 0.35
) -> list:
    """Keep only segments where at least `coverage_thresh` of sampled points
    lie on actual ink (white pixels in line_mask)."""
    kept = []
    for seg in segments:
        x1, y1 = seg[0][0] * w, seg[0][1] * h
        x2, y2 = seg[1][0] * w, seg[1][1] * h
        length = max(1, int(np.hypot(x2 - x1, y2 - y1)))
        n_samples = max(10, length)
        on_ink = 0
        for i in range(n_samples):
            t = i / (n_samples - 1) if n_samples > 1 else 0.5
            sx = int(x1 + t * (x2 - x1))
            sy = int(y1 + t * (y2 - y1))
            sx = max(0, min(w - 1, sx))
            sy = max(0, min(h - 1, sy))
            if line_mask[sy, sx] > 0:
                on_ink += 1
        ratio = on_ink / n_samples
        if ratio >= coverage_thresh:
            kept.append(seg)
    return kept


def _skeletonize(binary: np.ndarray) -> np.ndarray:
    """Morphological skeletonization — reduce binary blobs to 1px-wide lines."""
    skel = np.zeros_like(binary)
    element = cv2.getStructuringElement(cv2.MORPH_CROSS, (3, 3))
    img = binary.copy()
    while True:
        eroded = cv2.erode(img, element)
        opened = cv2.dilate(eroded, element)
        temp = cv2.subtract(img, opened)
        skel = cv2.bitwise_or(skel, temp)
        img = eroded.copy()
        if cv2.countNonZero(img) == 0:
            break
    return skel


def _merge_nearby_segments(segments: list, dist_thresh: float = 0.025) -> list:
    """Merge line segments that have very close endpoints (likely the same line fragmented)."""
    if not segments:
        return segments

    merged = []
    used = [False] * len(segments)

    for i in range(len(segments)):
        if used[i]:
            continue
        seg_a = segments[i]
        # Try to extend this segment by merging with nearby ones
        a1 = np.array(seg_a[0])
        a2 = np.array(seg_a[1])

        for j in range(i + 1, len(segments)):
            if used[j]:
                continue
            seg_b = segments[j]
            b1 = np.array(seg_b[0])
            b2 = np.array(seg_b[1])

            # Check if any pair of endpoints are close
            dists = [
                np.linalg.norm(a1 - b1),
                np.linalg.norm(a1 - b2),
                np.linalg.norm(a2 - b1),
                np.linalg.norm(a2 - b2),
            ]
            if min(dists) < dist_thresh:
                # Merge: take the two most distant endpoints
                pts = [a1, a2, b1, b2]
                max_d = 0
                best = (0, 1)
                for p in range(len(pts)):
                    for q in range(p + 1, len(pts)):
                        d = np.linalg.norm(pts[p] - pts[q])
                        if d > max_d:
                            max_d = d
                            best = (p, q)
                a1 = pts[best[0]]
                a2 = pts[best[1]]
                used[j] = True

        merged.append([
            [round(float(a1[0]), 4), round(float(a1[1]), 4)],
            [round(float(a2[0]), 4), round(float(a2[1]), 4)],
        ])
        used[i] = True

    return merged


def detect_inclusions(view: np.ndarray) -> list[dict]:
    """Detect red inclusion markers using BGR color filtering.

    IGI clarity plot markers are pure-ish red: high R channel with low-ish
    G and B (e.g. BGR=(8,8,167), (37,37,177)). We detect pixels where
    R dominates over both G and B, then cluster them into markers.
    """
    h, w = view.shape[:2]
    b, g, r = [ch.astype(np.int16) for ch in cv2.split(view)]

    # Pixel is "red" if R is significantly above G and B, and R is not too dim
    red_mask = ((r - g) > 15) & ((r - b) > 15) & (r > 120)
    red_mask = red_mask.astype(np.uint8) * 255

    # Dilate to connect nearby pixels into clusters (markers are small)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    red_mask = cv2.dilate(red_mask, kernel, iterations=1)

    contours, _ = cv2.findContours(red_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    inclusions = []
    for cnt in contours:
        area = cv2.contourArea(cnt)
        if area < 2:  # Skip single-pixel noise
            continue
        M = cv2.moments(cnt)
        if M["m00"] == 0:
            continue
        cx = M["m10"] / M["m00"]
        cy = M["m01"] / M["m00"]
        radius = max(1.0, np.sqrt(area / np.pi))

        inclusions.append({
            "x": round(cx / w, 4),
            "y": round(cy / h, 4),
            "r": round(radius / max(w, h), 4),
            "type": "internal",
        })

    return inclusions


def process_view(view: np.ndarray, view_type: str) -> dict:
    """Process a single view (table or pavilion) and return vector data."""
    print(f"\n  Processing {view_type} view ({view.shape[1]}x{view.shape[0]}px)...")

    outline = extract_outline(view)
    print(f"    Outline: {len(outline)} points")

    facet_lines = extract_facet_lines(view, outline)
    print(f"    Facet lines: {len(facet_lines)} segments")

    inclusions = detect_inclusions(view)
    print(f"    Inclusions: {len(inclusions)} markers")

    return {
        "type": view_type,
        "outline": outline,
        "facet_lines": facet_lines,
        "inclusions": inclusions,
    }


def draw_debug_overlay(img: np.ndarray, views_data: list[dict], left_w: int) -> np.ndarray:
    """Draw vector data back onto the original image for visual validation."""
    debug = img.copy()
    h, full_w = debug.shape[:2]

    for view_data in views_data:
        is_table = view_data["type"] == "table"
        # Offset for pavilion view
        x_off = 0 if is_table else left_w
        view_w = left_w if is_table else (full_w - left_w)
        view_h = h

        # Draw outline in green
        outline = view_data["outline"]
        if outline:
            pts = [(int(p[0] * view_w) + x_off, int(p[1] * view_h)) for p in outline]
            for i in range(len(pts)):
                p1 = pts[i]
                p2 = pts[(i + 1) % len(pts)]
                cv2.line(debug, p1, p2, (0, 255, 0), 2)

        # Draw facet lines in blue
        for seg in view_data["facet_lines"]:
            p1 = (int(seg[0][0] * view_w) + x_off, int(seg[0][1] * view_h))
            p2 = (int(seg[1][0] * view_w) + x_off, int(seg[1][1] * view_h))
            cv2.line(debug, p1, p2, (255, 100, 0), 1)

        # Draw inclusions in red
        for inc in view_data["inclusions"]:
            cx = int(inc["x"] * view_w) + x_off
            cy = int(inc["y"] * view_h)
            r = max(3, int(inc["r"] * max(view_w, view_h)))
            cv2.circle(debug, (cx, cy), r, (0, 0, 255), 2)
            cv2.circle(debug, (cx, cy), 1, (0, 0, 255), -1)

    return debug


def main():
    args = parse_args()
    report_number = args.report_number
    report_dir = Path("reports") / report_number

    if not report_dir.exists():
        print(f"ERROR: Report directory not found at {report_dir}")
        sys.exit(1)

    img_path = find_clarity_image(report_dir)
    print(f"Reading clarity plot: {img_path}")

    img = cv2.imread(str(img_path))
    if img is None:
        print(f"ERROR: Could not read image at {img_path}")
        sys.exit(1)

    print(f"Image size: {img.shape[1]}x{img.shape[0]}px")

    # Split into table (left) and pavilion (right) views
    table_view, pavilion_view = split_views(img)
    left_w = table_view.shape[1]

    # Process each view
    table_data = process_view(table_view, "table")
    pavilion_data = process_view(pavilion_view, "pavilion")

    # Build output JSON
    result = {
        "report_number": report_number,
        "source_image": img_path.name,
        "views": [table_data, pavilion_data],
    }

    # Save JSON
    json_path = report_dir / "clarity_vectors.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)
    print(f"\nSaved vector data to {json_path}")

    # Save debug overlay
    debug_img = draw_debug_overlay(img, [table_data, pavilion_data], left_w)
    debug_path = report_dir / "clarity_debug.png"
    cv2.imwrite(str(debug_path), debug_img)
    print(f"Saved debug overlay to {debug_path}")

    # Summary
    for v in result["views"]:
        print(f"\n  {v['type'].upper()} VIEW:")
        print(f"    Outline points: {len(v['outline'])}")
        print(f"    Facet lines:    {len(v['facet_lines'])}")
        print(f"    Inclusions:     {len(v['inclusions'])}")


if __name__ == "__main__":
    main()
