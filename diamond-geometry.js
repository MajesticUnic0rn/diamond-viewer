/**
 * diamond-geometry.js
 *
 * Parametric brilliant-cut diamond geometry generator.
 * Takes IGI report JSON (proportions, shape, measurements) and produces
 * a Three.js BufferGeometry with proper facet structure and flat shading.
 *
 * Supports: Round, Cushion, Oval, Pear, Marquise, Emerald (simplified)
 */

// ── Shape Classification ──

export function classifyShape(shapeStr) {
    const s = (shapeStr || '').toUpperCase();
    if (s.includes('CUSHION'))   return 'cushion';
    if (s.includes('OVAL'))      return 'oval';
    if (s.includes('PEAR'))      return 'pear';
    if (s.includes('MARQUISE'))  return 'marquise';
    if (s.includes('EMERALD'))   return 'emerald';
    if (s.includes('PRINCESS'))  return 'princess';
    if (s.includes('RADIANT'))   return 'cushion';
    if (s.includes('HEART'))     return 'pear';
    return 'round';
}

// ── Girdle Outline Functions ──
// Returns a NORMALIZED radius multiplier at angle theta.
// 1.0 = average girdle radius R. Values > 1 along the longer axis,
// < 1 along the shorter axis. For round diamonds, always 1.0.

function girdleRadius(theta, shape, aspectRatio) {
    const c = Math.cos(theta);
    const s = Math.sin(theta);

    // Normalized semi-axes: ensures that at theta=0 we get L/(2R)
    // and at theta=90 we get W/(2R), where R = avgDiameter/2.
    const a = (2 * aspectRatio) / (aspectRatio + 1);  // > 1 for elongated
    const b = 2 / (aspectRatio + 1);                   // < 1 for elongated

    switch (shape) {
        case 'round':
            return 1.0;

        case 'cushion': {
            // Superellipse: |x/a|^n + |z/b|^n = 1, n≈2.5 for cushion corners
            const n = 2.5;
            const ca = Math.abs(c) / a;
            const sb = Math.abs(s) / b;
            if (ca < 1e-10 && sb < 1e-10) return a;
            return Math.pow(
                Math.pow(ca, n) + Math.pow(sb, n),
                -1 / n
            );
        }

        case 'oval':
        case 'marquise': {
            const bc = b * c;
            const as = a * s;
            return (a * b) / Math.sqrt(bc * bc + as * as);
        }

        case 'pear': {
            const taper = 0.55 + 0.45 * (0.5 + 0.5 * c);
            const bMod = b * taper;
            const bc = bMod * c;
            const as = a * s;
            const denom = Math.sqrt(bc * bc + as * as);
            return denom > 1e-10 ? (a * bMod) / denom : a;
        }

        case 'emerald':
        case 'princess': {
            const n = 4.0;
            const ca = Math.abs(c) / a;
            const sb = Math.abs(s) / b;
            if (ca < 1e-10 && sb < 1e-10) return a;
            return Math.pow(
                Math.pow(ca, n) + Math.pow(sb, n),
                -1 / n
            );
        }

        default:
            return 1.0;
    }
}

// ── Dimension Computation ──

function computeDimensions(report) {
    const L = report.length_mm || 6.5;
    const W = report.width_mm || 6.5;
    const p = report.proportions || {};

    const girdleDiameter = (L + W) / 2;
    const R = girdleDiameter / 2;

    const tablePct        = p.table_pct || 57;
    const crownHeightPct  = p.crown_height_pct || 15;
    const pavilionDepthPct = p.pavilion_depth_pct || 43;
    const totalDepthPct   = p.total_depth_pct || 62;

    const crownHeight  = (crownHeightPct / 100) * girdleDiameter;
    const pavilionDepth = (pavilionDepthPct / 100) * girdleDiameter;
    const tableRadius  = (tablePct / 100) * R;

    const totalDepth = (totalDepthPct / 100) * girdleDiameter;
    let girdleThick = totalDepth - crownHeight - pavilionDepth;
    girdleThick = Math.max(girdleThick, 0.01 * girdleDiameter);

    const yTable     =  crownHeight + girdleThick / 2;
    const yGirdleTop =  girdleThick / 2;
    const yGirdleBot = -girdleThick / 2;
    const yCulet     = -(pavilionDepth + girdleThick / 2);

    const aspectRatio = L / W;

    return {
        R, tableRadius, crownHeight, pavilionDepth, girdleThick,
        yTable, yGirdleTop, yGirdleBot, yCulet,
        aspectRatio, girdleDiameter
    };
}

// ── Vertex Generation ──
// Uses 16-fold girdle (32 girdle points) for more facets and a more realistic cut.

const FACETS = 16;          // main directions (doubled from 8)
const GIRDLE_PTS = FACETS * 2;  // 32 girdle points
const STEP_DEG = 360 / FACETS;  // 22.5°
const GIRDLE_STEP = 360 / GIRDLE_PTS; // 11.25°

function generateVertices(dims, shape, aspectRatio) {
    const { R, tableRadius, yTable, yGirdleTop, yGirdleBot, yCulet,
            crownHeight, pavilionDepth } = dims;
    const DEG = Math.PI / 180;
    const verts = {};

    function gR(theta) { return R * girdleRadius(theta, shape, aspectRatio); }
    function tR(theta) { return tableRadius * girdleRadius(theta, shape, aspectRatio); }

    // ── Table: center + edge points ──
    verts.tableCenter = [0, yTable, 0];
    verts.tableEdge = [];
    for (let i = 0; i < FACETS; i++) {
        const theta = i * STEP_DEG * DEG;
        const r = tR(theta);
        verts.tableEdge.push([r * Math.cos(theta), yTable, r * Math.sin(theta)]);
    }

    // ── Star tips: between table edges, partway down the crown ──
    verts.starTips = [];
    const starRadialPct = 0.50;
    const starHeightPct = 0.42;
    for (let i = 0; i < FACETS; i++) {
        const theta = (i * STEP_DEG + STEP_DEG / 2) * DEG;
        const rT = tR(theta);
        const rG = gR(theta);
        const r = rT + (rG - rT) * starRadialPct;
        const y = yTable - crownHeight * starHeightPct;
        verts.starTips.push([r * Math.cos(theta), y, r * Math.sin(theta)]);
    }

    // ── Upper girdle: GIRDLE_PTS points ──
    verts.upperGirdle = [];
    for (let i = 0; i < GIRDLE_PTS; i++) {
        const theta = i * GIRDLE_STEP * DEG;
        const r = gR(theta);
        verts.upperGirdle.push([r * Math.cos(theta), yGirdleTop, r * Math.sin(theta)]);
    }

    // ── Lower girdle: GIRDLE_PTS points ──
    verts.lowerGirdle = [];
    for (let i = 0; i < GIRDLE_PTS; i++) {
        const theta = i * GIRDLE_STEP * DEG;
        const r = gR(theta);
        verts.lowerGirdle.push([r * Math.cos(theta), yGirdleBot, r * Math.sin(theta)]);
    }

    // ── Pavilion tips: at subsidiary angles ──
    verts.pavilionTips = [];
    const pavRadialPct = 0.22;
    const pavHeightPct = 0.72;
    for (let i = 0; i < FACETS; i++) {
        const theta = (i * STEP_DEG + STEP_DEG / 2) * DEG;
        const rG = gR(theta);
        const r = rG * pavRadialPct;
        const y = yGirdleBot - pavilionDepth * pavHeightPct;
        verts.pavilionTips.push([r * Math.cos(theta), y, r * Math.sin(theta)]);
    }

    // ── Culet ──
    verts.culet = [0, yCulet, 0];

    return verts;
}

// ── Triangle Assembly ──

function buildTriangles(verts, yCenterHint) {
    const positions = [];
    const normals = [];

    // Add a triangle with computed face normal, ensuring outward orientation
    function tri(a, b, c) {
        const ux = b[0] - a[0], uy = b[1] - a[1], uz = b[2] - a[2];
        const vx = c[0] - a[0], vy = c[1] - a[1], vz = c[2] - a[2];
        let nx = uy * vz - uz * vy;
        let ny = uz * vx - ux * vz;
        let nz = ux * vy - uy * vx;
        const len = Math.sqrt(nx * nx + ny * ny + nz * nz);
        if (len > 1e-10) { nx /= len; ny /= len; nz /= len; }

        // Check outward direction: vector from diamond center to face centroid
        const cx = (a[0] + b[0] + c[0]) / 3;
        const cy = (a[1] + b[1] + c[1]) / 3 - yCenterHint;
        const cz = (a[2] + b[2] + c[2]) / 3;
        if (nx * cx + ny * cy + nz * cz < 0) {
            positions.push(...a, ...c, ...b);
            normals.push(-nx, -ny, -nz, -nx, -ny, -nz, -nx, -ny, -nz);
        } else {
            positions.push(...a, ...b, ...c);
            normals.push(nx, ny, nz, nx, ny, nz, nx, ny, nz);
        }
    }

    const TC = verts.tableCenter;
    const TE = verts.tableEdge;
    const ST = verts.starTips;
    const UG = verts.upperGirdle;
    const LG = verts.lowerGirdle;
    const PT = verts.pavilionTips;
    const CU = verts.culet;
    const N = FACETS;
    const G = GIRDLE_PTS;

    // ── 1. TABLE ──
    for (let i = 0; i < N; i++) {
        tri(TC, TE[i], TE[(i + 1) % N]);
    }

    // ── 2. STAR FACETS ──
    for (let i = 0; i < N; i++) {
        tri(TE[i], ST[i], TE[(i + 1) % N]);
    }

    // ── 3. BEZEL / KITE FACETS ──
    for (let i = 0; i < N; i++) {
        const top    = TE[i];
        const left   = ST[(i - 1 + N) % N];
        const right  = ST[i];
        const bottom = UG[2 * i];
        tri(top, left, bottom);
        tri(top, bottom, right);
    }

    // ── 4. UPPER GIRDLE FACETS ──
    for (let i = 0; i < N; i++) {
        const star   = ST[i];
        const gLeft  = UG[2 * i];
        const gMid   = UG[2 * i + 1];
        const gRight = UG[(2 * i + 2) % G];
        tri(star, gLeft, gMid);
        tri(star, gMid, gRight);
    }

    // ── 5. GIRDLE BAND ──
    for (let i = 0; i < G; i++) {
        const j = (i + 1) % G;
        tri(UG[i], LG[i], UG[j]);
        tri(LG[i], LG[j], UG[j]);
    }

    // ── 6. LOWER GIRDLE FACETS ──
    for (let i = 0; i < N; i++) {
        const pavTip = PT[i];
        const gLeft  = LG[2 * i];
        const gMid   = LG[2 * i + 1];
        const gRight = LG[(2 * i + 2) % G];
        tri(pavTip, gMid, gLeft);
        tri(pavTip, gRight, gMid);
    }

    // ── 7. PAVILION MAIN FACETS ──
    for (let i = 0; i < N; i++) {
        const top    = LG[2 * i];
        const left   = PT[(i - 1 + N) % N];
        const right  = PT[i];
        const bottom = CU;
        tri(top, left, bottom);
        tri(top, bottom, right);
    }

    return { positions, normals };
}

// ── Public API ──

export function createDiamondGeometry(reportData, THREE) {
    const shape = classifyShape(reportData.shape);
    const dims = computeDimensions(reportData);
    const verts = generateVertices(dims, shape, dims.aspectRatio);

    // Y center hint for outward-normal calculation (center of the diamond)
    const yCenterHint = (dims.yTable + dims.yCulet) / 2;

    const { positions, normals } = buildTriangles(verts, yCenterHint);

    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position',
        new THREE.Float32BufferAttribute(new Float32Array(positions), 3));
    geometry.setAttribute('normal',
        new THREE.Float32BufferAttribute(new Float32Array(normals), 3));

    // Center at origin
    geometry.computeBoundingBox();
    const center = new THREE.Vector3();
    geometry.boundingBox.getCenter(center);
    geometry.translate(-center.x, -center.y, -center.z);

    return geometry;
}
