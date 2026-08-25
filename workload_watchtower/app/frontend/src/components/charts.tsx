// charts.tsx — dependency-free inline-SVG charts tuned for the dark console theme.

interface Pt {
  label: string;
  value: number;
}

// Sparkline-style area/line chart for a time series.
export function LineChart({ data, color = "#2272EB", height = 160 }: { data: Pt[]; color?: string; height?: number }) {
  const w = 640;
  const h = height;
  const padX = 8;
  const padTop = 12;
  const padBottom = 22;
  const n = data.length;
  const max = Math.max(1, ...data.map((d) => d.value));
  const plotH = h - padTop - padBottom;
  const stepX = n > 1 ? (w - padX * 2) / (n - 1) : 0;

  const x = (i: number) => padX + i * stepX;
  const y = (v: number) => padTop + plotH * (1 - v / max);

  const line = data.map((d, i) => `${i === 0 ? "M" : "L"}${x(i).toFixed(1)},${y(d.value).toFixed(1)}`).join(" ");
  const area =
    n > 0
      ? `${line} L${x(n - 1).toFixed(1)},${(padTop + plotH).toFixed(1)} L${x(0).toFixed(1)},${(padTop + plotH).toFixed(1)} Z`
      : "";
  const gid = `grad-${color.replace("#", "")}`;

  return (
    <svg viewBox={`0 0 ${w} ${h}`} className="w-full" preserveAspectRatio="none" role="img" aria-label="Trend over time">
      <defs>
        <linearGradient id={gid} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity="0.28" />
          <stop offset="100%" stopColor={color} stopOpacity="0" />
        </linearGradient>
      </defs>
      {[0.25, 0.5, 0.75].map((f) => (
        <line key={f} x1={padX} x2={w - padX} y1={padTop + plotH * f} y2={padTop + plotH * f} stroke="var(--border)" strokeWidth={1} />
      ))}
      {n > 0 && <path d={area} fill={`url(#${gid})`} />}
      {n > 0 && <path d={line} fill="none" stroke={color} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />}
      {data.map((d, i) => (
        <circle key={i} cx={x(i)} cy={y(d.value)} r={2.5} fill={color} />
      ))}
    </svg>
  );
}

// Horizontal labelled bars (used for the by-type breakdown).
export function BarList({
  data,
  color = "#FF5F46",
  fmt = (v: number) => String(v),
}: {
  data: Pt[];
  color?: string;
  fmt?: (v: number) => string;
}) {
  const max = Math.max(1, ...data.map((d) => d.value));
  return (
    <div className="flex flex-col gap-3">
      {data.map((d) => (
        <div key={d.label}>
          <div className="mb-1 flex items-center justify-between text-[13px]">
            <span className="text-text-primary">{d.label}</span>
            <span className="tabular-nums text-text-secondary">{fmt(d.value)}</span>
          </div>
          <div className="h-2 w-full overflow-hidden rounded-full bg-app">
            <div
              className="h-full rounded-full transition-all"
              style={{ width: `${Math.max(3, (d.value / max) * 100)}%`, backgroundColor: color }}
            />
          </div>
        </div>
      ))}
    </div>
  );
}
