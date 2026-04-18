/**
 * Tiny SVG/CSS chart primitives — editorial/analytical aesthetic.
 * Intentionally library-free: thin strokes, tabular numbers, muted fills.
 */
import { cn } from "@/lib/utils";

// ── DailyBars ───────────────────────────────────────────────

interface DailyBarsProps {
  data: { day: string; count: number }[];
  days?: number;
  className?: string;
  barClassName?: string;
  /** Optional label suffix e.g. "reviews" */
  unit?: string;
}

/**
 * Renders a daily bar chart over the last N days (default 30).
 * Fills in missing days with zero so the cadence shows gaps honestly.
 */
export function DailyBars({
  data,
  days = 30,
  className,
  barClassName,
  unit = "",
}: DailyBarsProps) {
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const buckets: { day: Date; count: number }[] = [];
  const lookup = new Map(data.map((d) => [d.day, d.count]));
  for (let i = days - 1; i >= 0; i--) {
    const d = new Date(today);
    d.setDate(d.getDate() - i);
    const key = d.toISOString().slice(0, 10);
    buckets.push({ day: d, count: lookup.get(key) ?? 0 });
  }
  const max = Math.max(1, ...buckets.map((b) => b.count));

  return (
    <div className={cn("space-y-2", className)}>
      <div className="flex h-32 items-end gap-[2px]">
        {buckets.map((b, i) => {
          const pct = (b.count / max) * 100;
          const isToday = i === buckets.length - 1;
          return (
            <div
              key={b.day.toISOString()}
              className="group relative flex-1"
              title={`${b.day.toDateString()}: ${b.count}${unit ? ` ${unit}` : ""}`}
            >
              <div
                className={cn(
                  "w-full rounded-t-sm transition-colors",
                  isToday ? "bg-primary" : "bg-primary/30 group-hover:bg-primary/60",
                  barClassName,
                )}
                style={{ height: `${Math.max(pct, b.count > 0 ? 6 : 2)}%` }}
              />
            </div>
          );
        })}
      </div>
      <div className="flex justify-between text-[10px] uppercase tracking-wider text-muted-foreground">
        <span>{buckets[0].day.toLocaleDateString(undefined, { month: "short", day: "numeric" })}</span>
        <span>Today</span>
      </div>
    </div>
  );
}

// ── DonutChart ──────────────────────────────────────────────

interface DonutSlice {
  label: string;
  value: number;
  color: string;  // CSS color (e.g., 'var(--primary)' or 'rgb(...)')
}

interface DonutChartProps {
  slices: DonutSlice[];
  size?: number;
  strokeWidth?: number;
  centerLabel?: string;
  centerValue?: string | number;
}

export function DonutChart({
  slices,
  size = 120,
  strokeWidth = 16,
  centerLabel,
  centerValue,
}: DonutChartProps) {
  const total = slices.reduce((s, x) => s + x.value, 0) || 1;
  const r = (size - strokeWidth) / 2;
  const circ = 2 * Math.PI * r;

  let offset = 0;
  return (
    <div className="flex items-center gap-4">
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} className="shrink-0">
        {/* Background ring */}
        <circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          fill="none"
          stroke="var(--muted)"
          strokeWidth={strokeWidth}
        />
        {slices.map((s, i) => {
          const len = (s.value / total) * circ;
          const dasharray = `${len} ${circ - len}`;
          const dashoffset = -offset;
          offset += len;
          return (
            <circle
              key={i}
              cx={size / 2}
              cy={size / 2}
              r={r}
              fill="none"
              stroke={s.color}
              strokeWidth={strokeWidth}
              strokeDasharray={dasharray}
              strokeDashoffset={dashoffset}
              transform={`rotate(-90 ${size / 2} ${size / 2})`}
              strokeLinecap="butt"
            />
          );
        })}
        {centerValue !== undefined && (
          <text
            x="50%"
            y="50%"
            textAnchor="middle"
            dominantBaseline="central"
            className="fill-foreground"
            style={{ fontSize: size / 4, fontWeight: 700 }}
          >
            {centerValue}
          </text>
        )}
      </svg>
      {slices.some((s) => s.value > 0) ? (
        <div className="space-y-1 text-sm">
          {slices.map((s) => (
            <div key={s.label} className="flex items-center gap-2">
              <span
                className="h-2.5 w-2.5 rounded-sm"
                style={{ backgroundColor: s.color }}
              />
              <span className="font-medium">{s.label}</span>
              <span className="tabular-nums text-muted-foreground">
                {s.value} ({total > 0 ? Math.round((s.value / total) * 100) : 0}%)
              </span>
            </div>
          ))}
          {centerLabel && (
            <p className="pt-1 text-xs text-muted-foreground">{centerLabel}</p>
          )}
        </div>
      ) : (
        <p className="text-sm text-muted-foreground">No data yet.</p>
      )}
    </div>
  );
}

// ── ProgressSegments ────────────────────────────────────────

interface Segment {
  label: string;
  value: number;
  color: string;  // CSS color
}

export function ProgressSegments({
  segments,
  className,
}: {
  segments: Segment[];
  className?: string;
}) {
  const total = segments.reduce((s, x) => s + x.value, 0) || 1;
  return (
    <div className={cn("space-y-3", className)}>
      <div className="flex h-3 w-full overflow-hidden rounded-full bg-muted">
        {segments.map((s) => (
          <div
            key={s.label}
            title={`${s.label}: ${s.value}`}
            style={{
              width: `${(s.value / total) * 100}%`,
              backgroundColor: s.color,
            }}
          />
        ))}
      </div>
      <div className="flex flex-wrap gap-x-5 gap-y-1 text-sm">
        {segments.map((s) => (
          <div key={s.label} className="flex items-baseline gap-1.5">
            <span
              className="h-2.5 w-2.5 translate-y-0.5 rounded-sm"
              style={{ backgroundColor: s.color }}
            />
            <span className="font-medium">{s.label}</span>
            <span className="tabular-nums text-muted-foreground">{s.value}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

// ── HorizontalBars (for checklist pass rates) ───────────────

interface HorizontalBarRow {
  label: string;
  value: number;  // 0-100 typically
  secondary?: string;  // e.g., "142/150"
  /** 0-1 if value represents a ratio */
  maxValue?: number;
}

export function HorizontalBars({
  rows,
  className,
}: {
  rows: HorizontalBarRow[];
  className?: string;
}) {
  return (
    <div className={cn("space-y-2.5", className)}>
      {rows.map((r) => {
        const max = r.maxValue ?? 100;
        const pct = Math.min(100, Math.max(0, (r.value / max) * 100));
        return (
          <div key={r.label}>
            <div className="mb-1 flex items-baseline justify-between text-sm">
              <span className="truncate font-medium">{r.label}</span>
              <span className="tabular-nums text-muted-foreground">
                {r.secondary ?? `${pct.toFixed(0)}%`}
              </span>
            </div>
            <div className="h-1.5 w-full overflow-hidden rounded-full bg-muted">
              <div
                className="h-full rounded-full bg-primary/70 transition-all"
                style={{ width: `${pct}%` }}
              />
            </div>
          </div>
        );
      })}
    </div>
  );
}

// ── Metric (big number card) ────────────────────────────────

interface MetricProps {
  label: string;
  value: string | number;
  suffix?: string;
  accent?: "primary" | "amber" | "sky" | "emerald" | "slate";
  hint?: string;
}

const ACCENT: Record<NonNullable<MetricProps["accent"]>, string> = {
  primary: "text-primary",
  amber: "text-amber-600 dark:text-amber-400",
  sky: "text-sky-600 dark:text-sky-400",
  emerald: "text-emerald-600 dark:text-emerald-400",
  slate: "text-slate-700 dark:text-slate-200",
};

export function Metric({ label, value, suffix, accent = "primary", hint }: MetricProps) {
  return (
    <div className="rounded-md border bg-card p-5">
      <p className="text-[10px] font-bold uppercase tracking-[0.12em] text-muted-foreground">
        {label}
      </p>
      <p className="mt-2 flex items-baseline gap-1">
        <span className={cn("text-4xl font-semibold tabular-nums tracking-tight", ACCENT[accent])}>
          {value}
        </span>
        {suffix && (
          <span className="text-sm font-medium text-muted-foreground">{suffix}</span>
        )}
      </p>
      {hint && <p className="mt-1 text-xs text-muted-foreground">{hint}</p>}
    </div>
  );
}

// ── Utility: format seconds as "1m 23s" / "45s" / "2h 14m" ──

export function formatDuration(seconds: number | null | undefined): string {
  if (seconds == null || !isFinite(seconds)) return "—";
  const s = Math.round(seconds);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  const rs = s % 60;
  if (m < 60) return rs ? `${m}m ${rs}s` : `${m}m`;
  const h = Math.floor(m / 60);
  const rm = m % 60;
  return rm ? `${h}h ${rm}m` : `${h}h`;
}
