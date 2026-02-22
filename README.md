# Diamond Report Viewer

Interactive 3D diamond viewer and IGI grading report toolkit. Scrapes IGI certificate data, extracts and vectorizes clarity plots from PDFs, and renders parametric 3D diamonds in the browser using Three.js with BVH-based refraction.

## Quick Start

```bash
# Serve and open in browser (no build step needed)
npx serve public
# Open http://localhost:3000
```

All JavaScript dependencies (Three.js, three-mesh-bvh) are loaded via CDN — no `npm install` required for the viewer.

## Project Structure

```
diamond-viewer/
├── public/                         # Static site root (Vercel serves this)
│   ├── index.html                  # Landing / redirect
│   ├── app.html                    # Main viewer — search by report number, 3D diamond + grading card
│   ├── demo.html                   # Standalone demo — renders LG689561771 directly (no search UI)
│   ├── js/
│   │   ├── diamond-geometry.js     # Parametric diamond geometry generator (round, cushion, oval, pear, etc.)
│   │   └── diamond-material.js     # BVH-based refraction material (three-mesh-bvh)
│   └── data/
│       └── LG689561771/
│           ├── LG689561771.json    # Report metadata (4Cs, measurements, proportions)
│           └── clarity_vectors.json# Vectorized clarity data (outlines, facet lines, inclusions)
├── scripts/                        # Python IGI scraping pipeline
│   ├── fetch_igi.py                # Lower-level IGI fetcher (requests + Playwright fallback)
│   ├── scrape_igi.py               # Scrapes IGI verification page for report JSON, clarity plot, PDF
│   ├── verify_pdf.py               # Extracts clarity plot PNG from the PDF using pdfplumber + OpenCV
│   └── vectorize_clarity.py        # Converts raster clarity plot to vector JSON (outlines, facets, inclusions)
├── pyproject.toml                  # Python deps (opencv, pdfplumber, playwright, requests)
├── package.json                    # JS deps (three, three-mesh-bvh)
└── vercel.json                     # Vercel deployment config
```

## Pages

### `public/app.html` — Main Viewer

Full report viewer with search. Enter any IGI report number to load the 3D diamond and grading card. Supports compare mode (two diamonds side-by-side).

### `public/demo.html` — Standalone Demo

Hardcoded to `LG689561771`. Renders the 3D diamond with a grading overlay — no search UI, no dependencies on scraped normalized data. Parses the raw IGI JSON directly and transforms it for the geometry generator.

## IGI Report Scraping Pipeline

The Python scripts fetch and process IGI diamond grading reports. They require Python 3.13+ and the dependencies in `pyproject.toml`.

### Setup

```bash
# Install uv if you don't have it
pip install uv

# Install Python dependencies
uv sync

# Install Playwright browsers (needed for Cloudflare bypass)
uv run playwright install chromium
```

### Environment Variables

Create a `.env` file (or export directly) with:

```bash
IGI_API_KEY=your_igi_api_key_here
```

The `IGI_API_KEY` is required for PDF downloads via `fetch_igi.py`.

### 1. Scrape (`scrape_igi.py`)

Fetches the IGI verification page, extracts the embedded JSON blob with all grading data, and optionally downloads the PDF and clarity plot image.

```bash
uv run python scripts/scrape_igi.py LG689561771 --headless
uv run python scripts/scrape_igi.py LG689561771 --headless --with-pdf --with-plot
```

### 2. Extract (`verify_pdf.py`)

Opens the PDF with pdfplumber, locates the clarity plot image, and saves it as `clarity_plot.png`.

```bash
uv run python scripts/verify_pdf.py LG689561771
```

### 3. Vectorize (`vectorize_clarity.py`)

Processes the raster clarity plot with OpenCV to extract:
- **Outlines** — diamond shape polygons (table and pavilion views)
- **Facet lines** — internal line segments
- **Inclusions** — red markers with x, y, radius

All coordinates normalized to 0-1. Output: `clarity_vectors.json`.

```bash
uv run python scripts/vectorize_clarity.py LG689561771
```

### 4. Render (browser)

The HTML pages fetch the report JSON and render a parametric 3D diamond using:
- `public/js/diamond-geometry.js` — generates BufferGeometry from proportions (table%, crown height, pavilion depth, girdle shape)
- `public/js/diamond-material.js` — custom ShaderMaterial with BVH ray tracing for realistic refraction, dispersion, and fresnel

## Deployment

Deployed on Vercel as a static site. Push to `main` to trigger a deploy.

## Dependencies

**Python** (managed via `uv`):
- `pdfplumber` — PDF parsing and image extraction
- `opencv-python-headless` — image processing for vectorization
- `playwright` — browser automation for scraping (Cloudflare bypass)
- `requests` — HTTP client

**JavaScript** (loaded via CDN):
- `three` — 3D rendering
- `three-mesh-bvh` — BVH acceleration for refraction ray tracing

## TODO

- [ ] Automate IGI scraping pipeline — add a script/workflow to scrape new reports and commit the JSON to `public/data/`
- [ ] Support more diamond shapes — currently renders cushion cut; add round brilliant, emerald, oval, pear, marquise
- [ ] Report selector UI — let users pick from available reports or enter a report number to load
- [ ] Clarity plot overlay — render clarity characteristics from IGI report onto the 3D model using `scripts/vectorize_clarity.py` output
- [ ] Color tinting — apply color grade (D–Z) as a subtle body tint on the diamond material
- [ ] Mobile touch controls — improve orbit controls for mobile (pinch zoom, two-finger rotate)
- [ ] PDF report viewer — display the IGI PDF certificate alongside the 3D diamond
- [ ] Environment map selector — let users switch between different HDRI lighting environments
- [ ] Compare mode polish — side-by-side comparison in `app.html` needs layout and UX work
- [ ] Fluorescence visualization — toggle UV lighting to show fluorescence grade
