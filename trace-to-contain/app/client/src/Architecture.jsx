import React from "react";

/*
 Polished inline-SVG architecture diagram (rendered in React, not drawio).
 On-brand Databricks-style line glyphs (authored here — no third-party logos).
 Navy (manufacturing) = data & lineage ; lava (Databricks) = intelligence & action.
*/

// ---- product glyphs, drawn in a 0..24 box, white line-art ----
const G = (name, c = "#fff") => {
  const s = { fill: "none", stroke: c, strokeWidth: 1.8, strokeLinecap: "round", strokeLinejoin: "round" };
  switch (name) {
    case "wafer": return <g {...s}><circle cx="12" cy="12" r="9" /><line x1="12" y1="1" x2="12" y2="5" /><line x1="12" y1="19" x2="12" y2="23" /><line x1="1" y1="12" x2="5" y2="12" /><line x1="19" y1="12" x2="23" y2="12" /><circle cx="12" cy="12" r="2.3" fill={c} stroke="none" /><circle cx="8" cy="8" r="1" fill={c} stroke="none" /><circle cx="16" cy="15" r="1" fill={c} stroke="none" /></g>;
    case "rma": return <g {...s}><path d="M6 3 h8 l4 4 v14 h-16 v-18 z" /><path d="M14 3 v4 h4" /><line x1="12" y1="11" x2="12" y2="15" /><circle cx="12" cy="18" r="0.6" fill={c} stroke="none" /></g>;
    case "volume": return <g {...s}><ellipse cx="12" cy="6" rx="8" ry="3" /><path d="M4 6 v12 c0 1.6 3.6 3 8 3 s8 -1.4 8 -3 v-12" /><path d="M4 12 c0 1.6 3.6 3 8 3 s8 -1.4 8 -3" /></g>;
    case "lakeflow": return <g {...s}><circle cx="5" cy="6" r="2.4" /><circle cx="5" cy="18" r="2.4" /><circle cx="18" cy="12" r="2.4" /><path d="M7 7 L15.6 11" /><path d="M7 17 L15.6 13" /><path d="M20.4 12 h2.2 M21 10.4 l1.6 1.6 -1.6 1.6" /></g>;
    case "delta": return <g><path d="M12 3 L22 21 H2 Z" fill={c} opacity="0.16" stroke={c} strokeWidth="1.8" strokeLinejoin="round" /><path d="M12 9 L17 19 H7 Z" fill="none" stroke={c} strokeWidth="1.4" strokeLinejoin="round" /></g>;
    case "corr": return <g {...s}><path d="M4 3 v18 h18" /><circle cx="8" cy="16" r="1.1" fill={c} stroke="none" /><circle cx="12" cy="12" r="1.1" fill={c} stroke="none" /><circle cx="15" cy="9" r="1.1" fill={c} stroke="none" /><circle cx="19" cy="6" r="1.1" fill={c} stroke="none" /><path d="M6 18 L20 5" strokeDasharray="2 2" /></g>;
    case "feature": return <g {...s}><rect x="3" y="4" width="18" height="16" rx="1.5" /><line x1="3" y1="10" x2="21" y2="10" /><line x1="9" y1="4" x2="9" y2="20" /><line x1="15" y1="4" x2="15" y2="20" /><path d="M18 3.5 l0.7 1.6 1.7 0.2 -1.3 1.2 0.4 1.7 -1.5 -0.9 -1.5 0.9 0.4 -1.7 -1.3 -1.2 1.7 -0.2 z" fill={c} stroke="none" /></g>;
    case "mlflow": return <g {...s}><path d="M20 12 a8 8 0 1 0 -3 6.2" /><path d="M17 13 l0.6 5 4.4 -1.2" /></g>;
    case "serving": return <g {...s}><circle cx="7" cy="12" r="2" fill={c} stroke="none" /><path d="M11 8 a6 6 0 0 1 0 8" /><path d="M14 5 a10 10 0 0 1 0 14" /></g>;
    case "lakebase": return <g {...s}><ellipse cx="12" cy="6" rx="7.5" ry="2.8" /><path d="M4.5 6 v12 c0 1.5 3.4 2.8 7.5 2.8 s7.5 -1.3 7.5 -2.8 v-12" /><path d="M4.5 12 c0 1.5 3.4 2.8 7.5 2.8 s7.5 -1.3 7.5 -2.8" /><path d="M13 9.5 l-3 4 h3 l-1.2 4 4 -5.2 h-3 z" fill={c} stroke="none" /></g>;
    case "app": return <g {...s}><rect x="3" y="4" width="18" height="16" rx="2" /><line x1="3" y1="8.5" x2="21" y2="8.5" /><circle cx="6" cy="6.2" r="0.7" fill={c} stroke="none" /><circle cx="8.4" cy="6.2" r="0.7" fill={c} stroke="none" /><path d="M9 13 l3 3 -3 3" transform="translate(0,-2) scale(0.9)" /></g>;
    case "agent": return <g {...s}><path d="M12 3 l1.6 4.2 4.4 1.4 -4.4 1.4 L12 14 l-1.6 -4.6 -4.4 -1.4 4.4 -1.4 z" fill={c} stroke="none" /><path d="M6 17 h12 a2 2 0 0 1 2 2 v0 a2 2 0 0 1 -2 2 H6 a2 2 0 0 1 -2 -2 v0 a2 2 0 0 1 2 -2 z" /><circle cx="8.5" cy="19" r="0.7" fill={c} stroke="none" /><circle cx="12" cy="19" r="0.7" fill={c} stroke="none" /><circle cx="15.5" cy="19" r="0.7" fill={c} stroke="none" /></g>;
    case "unity": return <g {...s}><path d="M12 2 L21 7 V17 L12 22 L3 17 V7 Z" /><path d="M9.5 12 l2 2 3.5 -4" /></g>;
    case "person": return <g {...s}><circle cx="12" cy="8" r="3.4" /><path d="M5 21 c0 -4 3.4 -6.5 7 -6.5 s7 2.5 7 6.5" /></g>;
    case "people": return <g {...s}><circle cx="9" cy="8" r="3" /><path d="M3 20 c0 -3.6 2.9 -5.6 6 -5.6 s6 2 6 5.6" /><circle cx="17" cy="9" r="2.4" /><path d="M15.5 14.6 c2.6 0.2 5 2 5 5" /></g>;
    default: return null;
  }
};

export default function Architecture() {
  // node color roles
  const NAVY = "#2f4f86", NAVYd = "#22406f", LAVA = "#d1442b", LAVAd = "#a83621",
        TEAL = "#2b6d63", SLATE = "#3a4353", GREEN = "#2f6d47", GOV = "#7a2f2f";
  const ZTOP = 104, NH = 84, NGAP = 22, NW_PAD = 16;
  const zones = [
    { x: 24, w: 236, title: "1 · Sources", accent: NAVY, tag: "manufacturing",
      nodes: [
        { g: "wafer", t: "ATE / wafer-probe", s: "STDF-like test", c: SLATE },
        { g: "rma", t: "MES · QMS", s: "RMA events", c: SLATE },
        { g: "volume", t: "Landing Volume", s: "raw files", c: SLATE },
      ] },
    { x: 284, w: 250, title: "2 · Lakehouse", accent: NAVY, tag: "system of record",
      nodes: [
        { img: "/dbx-icons/lakeflow.svg", t: "Lakeflow", s: "Auto Loader ingest", c: NAVY },
        { img: "/dbx-icons/delta-lake.svg", t: "Delta + Unity Catalog", s: "fab · genealogy · RMA", c: NAVY },
        { g: "corr", t: "Correlation", s: "confounder-conditioned", c: NAVYd },
        { img: "/dbx-icons/genie-agents.svg", t: "Genie · Ask the Data", s: "NL→SQL population Q&A", c: NAVY },
      ] },
    { x: 558, w: 250, title: "3 · Intelligence", accent: LAVA, tag: "Databricks",
      nodes: [
        { img: "/dbx-icons/feature-store.svg", t: "Feature Engineering", s: "die_features · 1 def", c: LAVA },
        { img: "/dbx-icons/mlflow.svg", t: "MLflow · Optuna", s: "ft_margin_predictor@prod", c: LAVA },
        { img: "/dbx-icons/model-serving.svg", t: "Model Serving", s: "FM API · Claude", c: TEAL },
      ] },
    { x: 832, w: 262, title: "4 · Operational", accent: LAVA, tag: "decision & action",
      nodes: [
        { img: "/dbx-icons/lakebase.svg", t: "Lakebase", s: "online features · cases", c: LAVA },
        { img: "/dbx-icons/genie-agents.svg", t: "Governed agent", s: "tiered recommendation", c: LAVAd },
        { img: "/dbx-icons/databricks-apps.svg", t: "Databricks App", s: "Trace to Contain", c: GREEN },
      ] },
    { x: 1118, w: 218, title: "Human in the loop", accent: GOV, tag: "authority",
      nodes: [
        { g: "person", t: "FA Engineer", s: "proposes", c: SLATE },
        { g: "person", t: "Quality Eng", s: "approves hold", c: SLATE },
        { g: "people", t: "MRB", s: "disposition", c: SLATE },
      ] },
  ];
  const ROWS = zones.reduce((m, z) => Math.max(m, z.nodes.length), 0);   // tallest zone (Lakehouse now 4, incl. Genie)
  const ZBODY = ROWS * NH + (ROWS - 1) * NGAP;                            // stacked node-band height
  const nodeGeom = (zi, ni) => {
    const z = zones[zi];
    const x = z.x + NW_PAD, w = z.w - 2 * NW_PAD;
    const y = ZTOP + 44 + ni * (NH + NGAP);
    return { x, y, w, h: NH, cx: x + w / 2, cy: y + NH / 2, rx: x + w, lx: x };
  };
  const W = 1360, H = ZTOP + 44 + ZBODY + 120;

  const flows = [
    // sources -> lakeflow
    [[0, 0], [1, 0]], [[0, 1], [1, 0]], [[0, 2], [1, 0]],
    // lakeflow -> delta (in-zone), delta -> corr
    // delta -> feature (point-in-time)
    [[1, 1], [2, 0], "point-in-time"],
    // feature -> mlflow (in-zone) ; feature -> lakebase (online) ; mlflow -> lakebase (predictions)
    [[2, 0], [3, 0], "features"],
    [[2, 1], [3, 0], "predictions", true],
    // corr -> agent ; serving -> agent
    [[1, 2], [3, 1], "", false, "dash"],
    [[2, 2], [3, 1], "narrative", false, "dash"],
    // app -> FA
    [[3, 2], [4, 0], "", true],
  ];

  return (
    <div style={{ overflowX: "auto", background: "var(--panel)", border: "1px solid var(--line)", borderRadius: 12 }}>
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" style={{ minWidth: 1120, display: "block" }} role="img"
        aria-label="Trace to Contain solution architecture on Databricks">
        <defs>
          <linearGradient id="bg" x1="0" y1="0" x2="1" y2="0">
            <stop offset="0" stopColor="#141b2b" /><stop offset="0.55" stopColor="#161824" /><stop offset="1" stopColor="#1d1517" />
          </linearGradient>
          <marker id="arw" markerWidth="9" markerHeight="9" refX="6" refY="3" orient="auto"><path d="M0 0 L6 3 L0 6 Z" fill="#8892a6" /></marker>
          <marker id="arwL" markerWidth="10" markerHeight="10" refX="6.5" refY="3.2" orient="auto"><path d="M0 0 L6.5 3.2 L0 6.4 Z" fill="#ff6a4d" /></marker>
          <marker id="arwLoop" markerWidth="10" markerHeight="10" refX="6.5" refY="3.2" orient="auto"><path d="M0 0 L6.5 3.2 L0 6.4 Z" fill="#ff3621" /></marker>
        </defs>
        <rect x="0" y="0" width={W} height={H} rx="12" fill="url(#bg)" />

        {/* title */}
        <text x="28" y="38" fill="#eef1f6" fontSize="21" fontWeight="700" fontFamily="DM Sans, sans-serif">Trace to Contain — Solution Architecture</text>
        <text x="28" y="60" fill="#8a93a6" fontSize="12.5" fontFamily="DM Mono, monospace">RF FAB-to-field intelligence on Databricks · synthetic demo</text>
        {/* legend — stacked two rows, right side, kept clear of the canvas edge (was one clipped/overlapping row) */}
        <g fontFamily="DM Mono, monospace" fontSize="11">
          <circle cx="1060" cy="30" r="4" fill="#2f4f86" /><text x="1072" y="34" fill="#9aa4b2">Manufacturing · data &amp; lineage</text>
          <circle cx="1060" cy="50" r="4" fill="#ff3621" /><text x="1072" y="54" fill="#9aa4b2">Databricks · intelligence &amp; action</text>
        </g>

        {/* zone containers */}
        {zones.map((z, zi) => (
          <g key={zi}>
            <rect x={z.x} y={ZTOP} width={z.w} height={ZBODY + 60} rx="12"
              fill="rgba(255,255,255,0.02)" stroke={z.accent} strokeOpacity="0.5" strokeWidth="1.4" />
            <text x={z.x + 16} y={ZTOP + 24} fill="#e7ebf0" fontSize="13.5" fontWeight="700" fontFamily="DM Sans, sans-serif">{z.title}</text>
            <text x={z.x + z.w - 12} y={ZTOP + 24} textAnchor="end" fill={z.accent === LAVA ? "#ff7a63" : "#7f93c4"} fontSize="10" fontFamily="DM Mono, monospace">{z.tag}</text>
          </g>
        ))}

        {/* flow connectors (under nodes) */}
        {flows.map((f, i) => {
          const a = nodeGeom(f[0][0], f[0][1]), b = nodeGeom(f[1][0], f[1][1]);
          const dash = f[4] === "dash";
          const emph = f[3] === true;
          const midx = (a.rx + b.lx) / 2;
          const d = `M ${a.rx} ${a.cy} C ${midx} ${a.cy}, ${midx} ${b.cy}, ${b.lx - 6} ${b.cy}`;
          return (
            <g key={"f" + i}>
              <path d={d} fill="none" stroke={emph ? "#ff6a4d" : "#5b6577"} strokeWidth={emph ? 2.2 : 1.5}
                strokeDasharray={dash ? "4 4" : "0"} markerEnd={emph ? "url(#arwL)" : "url(#arw)"} opacity={dash ? 0.8 : 1} />
            </g>
          );
        })}
        {/* in-zone vertical flows: lakeflow->delta->corr ; feature->mlflow ; lakebase->agent->app */}
        {[[1, 0, 1], [1, 1, 2], [2, 0, 1], [3, 0, 1], [3, 1, 2], [4, 0, 1], [4, 1, 2]].map(([zi, ai, bi], i) => {
          const a = nodeGeom(zi, ai), b = nodeGeom(zi, bi);
          return <path key={"v" + i} d={`M ${a.cx} ${a.y + a.h} L ${b.cx} ${b.y}`} fill="none" stroke="#4a5265" strokeWidth="1.5" markerEnd="url(#arw)" />;
        })}

        {/* nodes */}
        {zones.map((z, zi) => z.nodes.map((n, ni) => {
          const g = nodeGeom(zi, ni);
          return (
            <g key={zi + "-" + ni}>
              <rect x={g.x} y={g.y} width={g.w} height={g.h} rx="11" fill="#1b2130" stroke={n.c} strokeWidth="1.5" />
              <rect x={g.x} y={g.y} width="5" height={g.h} rx="2.5" fill={n.c} />
              {n.img
                ? <image href={n.img} x={g.x + 14} y={g.y + 21} width="42" height="42" />
                : <g transform={`translate(${g.x + 16},${g.y + 16}) scale(1.35)`}>{G(n.g)}</g>}
              <text x={g.x + 66} y={g.y + 34} fill="#eef1f6" fontSize="13" fontWeight="600" fontFamily="DM Sans, sans-serif">{n.t}</text>
              <text x={g.x + 66} y={g.y + 52} fill="#9aa4b2" fontSize="11" fontFamily="DM Mono, monospace">{n.s}</text>
            </g>
          );
        }))}

        {/* flow labels — drawn on top of the nodes with a background pill, so they read cleanly
            in the narrow inter-zone gaps instead of being cut by the node boxes */}
        {flows.filter((f) => f[2]).map((f, i) => {
          const a = nodeGeom(f[0][0], f[0][1]), b = nodeGeom(f[1][0], f[1][1]);
          const x = (a.rx + b.lx) / 2, y = (a.cy + b.cy) / 2;
          const wpill = f[2].length * 6.0 + 16;
          return (
            <g key={"fl" + i}>
              <rect x={x - wpill / 2} y={y - 9} width={wpill} height="18" rx="9"
                fill="#0f1420" stroke="#2b3242" strokeWidth="1" opacity="0.96" />
              <text x={x} y={y + 3.5} textAnchor="middle" fill="#c3cad6" fontSize="10" fontFamily="DM Mono, monospace">{f[2]}</text>
            </g>
          );
        })}

        {/* Unity Catalog governance strip */}
        {(() => {
          const gy = ZTOP + ZBODY + 64;
          return (
            <g>
              <rect x={zones[1].x} y={gy} width={zones[3].x + zones[3].w - zones[1].x} height="42" rx="9"
                fill="rgba(255,54,33,0.05)" stroke="#7a2f2f" strokeWidth="1.4" strokeDasharray="6 4" />
              <image href="/dbx-icons/unity-catalog.svg" x={zones[1].x + 12} y={gy + 7} width="28" height="28" />
              <text x={zones[1].x + 48} y={gy + 26} fill="#ffb3a6" fontSize="12" fontWeight="600" fontFamily="DM Sans, sans-serif">Unity Catalog — governance, lineage &amp; audit</text>
              <text x={zones[3].x + zones[3].w - 14} y={gy + 26} textAnchor="end" fill="#9aa4b2" fontSize="10.5" fontFamily="DM Mono, monospace">fab_measurements → features → model → prediction → disposition</text>
            </g>
          );
        })()}

        {/* close-the-loop arc: Quality approval -> back to lakehouse (MES + write-back) */}
        {(() => {
          const q = nodeGeom(4, 1), d = nodeGeom(1, 1);
          const y = H - 26;
          const path = `M ${q.cx} ${q.y + q.h} C ${q.cx} ${y}, ${d.cx} ${y}, ${d.cx} ${d.y + d.h + 4}`;
          return (
            <g>
              <path d={path} fill="none" stroke="#ff3621" strokeWidth="2.2" strokeDasharray="7 4" markerEnd="url(#arwLoop)" opacity="0.9" />
              <text x={(q.cx + d.cx) / 2} y={y - 6} textAnchor="middle" fill="#ff7a63" fontSize="11" fontWeight="600" fontFamily="DM Sans, sans-serif">approved hold → MES instruction + write-back (close the loop)</text>
            </g>
          );
        })()}
      </svg>
    </div>
  );
}
