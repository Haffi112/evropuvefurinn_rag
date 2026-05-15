import { useQuery } from "@tanstack/react-query";
import { formatDistanceToNow } from "date-fns";
import { Clock, ArrowUpRight } from "lucide-react";
import { apiFetch } from "@/lib/api";
import {
  DailyBars,
  Metric,
  ProgressSegments,
  formatDuration,
} from "@/components/charts";
import ModeBadge from "@/components/ModeBadge";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

interface ReviewStats {
  status_counts: { status: string; count: number }[];
  weekly_activity: { day: string; count: number }[];
  reviewers: {
    id: number;
    username: string;
    reviews: number;
    avg_duration_seconds: number | null;
    total_duration_seconds: number;
    last_activity: string | null;
  }[];
  oldest_pending: {
    id: number;
    query_text: string;
    mode: string;
    created_at: string;
    submitted_by: string | null;
  }[];
  total_evaluations: number;
  avg_duration_seconds: number | null;
  total_duration_seconds: number;
  coverage: {
    zero_evals: number;
    one_eval: number;
    multi_eval: number;
    total_queries: number;
    avg_annotators_per_reviewed_query: number;
  };
}

// Tailwind/CSS colors for the four review statuses — matched to the rest of
// the app's color grammar: amber=pending, sky=in-motion, emerald=approved,
// slate=excluded.
const STATUS_COLORS: Record<string, string> = {
  pending: "rgb(245 158 11)",    // amber-500
  reviewed: "rgb(14 165 233)",   // sky-500
  approved: "rgb(16 185 129)",   // emerald-500
  excluded: "rgb(100 116 139)",  // slate-500
};

// Multi-annotator coverage tiers: 0 = no eyes on it yet, 1 = single annotator,
// 2+ = inter-annotator overlap (suitable for IAA / kappa).
const COVERAGE_COLORS = {
  zero: "rgb(245 158 11)",     // amber-500 — uncovered
  one: "rgb(14 165 233)",      // sky-500 — partial coverage
  multi: "rgb(16 185 129)",    // emerald-500 — overlap reached
};

function countByStatus(rows: { status: string; count: number }[], key: string) {
  return rows.find((r) => r.status === key)?.count ?? 0;
}

export default function ReviewProgressPage() {
  const { data, isLoading } = useQuery<ReviewStats>({
    queryKey: ["admin-review-stats"],
    queryFn: () => apiFetch("/api/v1/admin/review-stats"),
    staleTime: 15_000,
    refetchInterval: 30_000,
  });

  if (isLoading) {
    return (
      <div className="space-y-6">
        <Skeleton className="h-10 w-72" />
        <div className="grid gap-4 sm:grid-cols-4">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-28" />
          ))}
        </div>
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }

  if (!data) return <p className="text-muted-foreground">Could not load stats.</p>;

  const pending = countByStatus(data.status_counts, "pending");
  const reviewed = countByStatus(data.status_counts, "reviewed");
  const approved = countByStatus(data.status_counts, "approved");
  const excluded = countByStatus(data.status_counts, "excluded");
  const totalQueries = pending + reviewed + approved + excluded;
  const completionPct = totalQueries > 0
    ? Math.round(((reviewed + approved) / totalQueries) * 100)
    : 0;

  return (
    <div className="space-y-8">
      {/* Header */}
      <div>
        <p className="text-[10px] font-bold uppercase tracking-[0.12em] text-muted-foreground">
          Review progress
        </p>
        <h1 className="mt-1 text-3xl font-bold">Review dashboard</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Aggregate view of the review queue: progress, reviewer throughput, and the oldest pending items.
        </p>
      </div>

      {/* Hero metrics */}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Metric
          label="Total evaluations"
          value={data.total_evaluations.toLocaleString()}
          hint={`avg ${data.coverage.avg_annotators_per_reviewed_query.toFixed(2)} annotators / reviewed query`}
          accent="primary"
        />
        <Metric
          label="Completion"
          value={`${completionPct}%`}
          hint={`${(reviewed + approved).toLocaleString()} of ${totalQueries.toLocaleString()} queries`}
          accent="emerald"
        />
        <Metric
          label="Awaiting review"
          value={pending.toLocaleString()}
          accent="amber"
        />
        <Metric
          label="Avg time / review"
          value={formatDuration(data.avg_duration_seconds)}
          hint={`${formatDuration(data.total_duration_seconds)} total`}
          accent="sky"
        />
      </div>

      {/* Status progress bar */}
      <Card className="p-5">
        <div className="mb-4 flex items-baseline justify-between">
          <h2 className="text-sm font-bold uppercase tracking-wider text-muted-foreground">
            Queue state
          </h2>
          <span className="text-xs text-muted-foreground">
            {totalQueries.toLocaleString()} queries in scope
          </span>
        </div>
        <ProgressSegments
          segments={[
            { label: "Approved", value: approved, color: STATUS_COLORS.approved },
            { label: "Reviewed", value: reviewed, color: STATUS_COLORS.reviewed },
            { label: "Pending", value: pending, color: STATUS_COLORS.pending },
            { label: "Excluded", value: excluded, color: STATUS_COLORS.excluded },
          ]}
        />
      </Card>

      {/* Annotator coverage — multi-annotator depth across the corpus */}
      <Card className="p-5">
        <div className="mb-4 flex items-baseline justify-between">
          <h2 className="text-sm font-bold uppercase tracking-wider text-muted-foreground">
            Annotator coverage
          </h2>
          <span className="text-xs text-muted-foreground">
            queries by number of distinct annotators
          </span>
        </div>
        <ProgressSegments
          segments={[
            { label: "2+ annotators", value: data.coverage.multi_eval, color: COVERAGE_COLORS.multi },
            { label: "1 annotator", value: data.coverage.one_eval, color: COVERAGE_COLORS.one },
            { label: "Not yet annotated", value: data.coverage.zero_evals, color: COVERAGE_COLORS.zero },
          ]}
        />
        <div className="mt-4 grid gap-3 sm:grid-cols-3">
          <div className="rounded-md border bg-muted/30 p-3">
            <div className="text-xs uppercase tracking-wider text-muted-foreground">
              Not annotated
            </div>
            <div className="mt-0.5 text-lg font-bold tabular-nums">
              {data.coverage.zero_evals.toLocaleString()}
            </div>
            <div className="text-xs text-muted-foreground">
              {data.coverage.total_queries > 0
                ? `${Math.round((100 * data.coverage.zero_evals) / data.coverage.total_queries)}% of corpus`
                : "—"}
            </div>
          </div>
          <div className="rounded-md border bg-muted/30 p-3">
            <div className="text-xs uppercase tracking-wider text-muted-foreground">
              Single annotator
            </div>
            <div className="mt-0.5 text-lg font-bold tabular-nums">
              {data.coverage.one_eval.toLocaleString()}
            </div>
            <div className="text-xs text-muted-foreground">
              {data.coverage.total_queries > 0
                ? `${Math.round((100 * data.coverage.one_eval) / data.coverage.total_queries)}% of corpus`
                : "—"}
            </div>
          </div>
          <div className="rounded-md border bg-muted/30 p-3">
            <div className="text-xs uppercase tracking-wider text-muted-foreground">
              2+ annotators (IAA-ready)
            </div>
            <div className="mt-0.5 text-lg font-bold tabular-nums">
              {data.coverage.multi_eval.toLocaleString()}
            </div>
            <div className="text-xs text-muted-foreground">
              {data.coverage.total_queries > 0
                ? `${Math.round((100 * data.coverage.multi_eval) / data.coverage.total_queries)}% of corpus`
                : "—"}
            </div>
          </div>
        </div>
      </Card>

      {/* Weekly activity */}
      <Card className="p-5">
        <div className="mb-4 flex items-baseline justify-between">
          <h2 className="text-sm font-bold uppercase tracking-wider text-muted-foreground">
            Review cadence
          </h2>
          <span className="text-xs text-muted-foreground">last 30 days · all reviewers</span>
        </div>
        <DailyBars data={data.weekly_activity} unit="reviews" />
      </Card>

      {/* Reviewer leaderboard */}
      <Card>
        <div className="flex items-baseline justify-between p-5 pb-3">
          <h2 className="text-sm font-bold uppercase tracking-wider text-muted-foreground">
            Reviewers
          </h2>
          <span className="text-xs text-muted-foreground">
            sorted by review count
          </span>
        </div>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Reviewer</TableHead>
              <TableHead className="text-right">Reviews</TableHead>
              <TableHead className="text-right">Avg time</TableHead>
              <TableHead className="text-right">Total time</TableHead>
              <TableHead>Last activity</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {data.reviewers.length === 0 ? (
              <TableRow>
                <TableCell
                  colSpan={5}
                  className="text-center text-sm text-muted-foreground"
                >
                  No reviewers yet.
                </TableCell>
              </TableRow>
            ) : (
              data.reviewers.map((r) => (
                <TableRow key={r.id}>
                  <TableCell className="font-medium">{r.username}</TableCell>
                  <TableCell className="text-right tabular-nums">
                    {r.reviews > 0 ? (
                      r.reviews.toLocaleString()
                    ) : (
                      <span className="text-muted-foreground">0</span>
                    )}
                  </TableCell>
                  <TableCell className="text-right tabular-nums text-muted-foreground">
                    {formatDuration(r.avg_duration_seconds)}
                  </TableCell>
                  <TableCell className="text-right tabular-nums text-muted-foreground">
                    {formatDuration(r.total_duration_seconds)}
                  </TableCell>
                  <TableCell className="text-sm text-muted-foreground">
                    {r.last_activity
                      ? formatDistanceToNow(new Date(r.last_activity), {
                          addSuffix: true,
                        })
                      : "—"}
                  </TableCell>
                </TableRow>
              ))
            )}
          </TableBody>
        </Table>
      </Card>

      {/* Oldest pending */}
      {data.oldest_pending.length > 0 && (
        <Card className="p-5">
          <div className="mb-4 flex items-baseline justify-between">
            <h2 className="flex items-center gap-2 text-sm font-bold uppercase tracking-wider text-muted-foreground">
              <Clock className="h-3.5 w-3.5" />
              Oldest pending
            </h2>
            <span className="text-xs text-muted-foreground">
              reviewer needs to pick these up
            </span>
          </div>
          <ul className="divide-y">
            {data.oldest_pending.map((q) => (
              <li key={q.id}>
                <a
                  href={`/review/queries/${q.id}`}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="flex items-center gap-3 py-3 transition-colors hover:bg-muted/40"
                >
                  <ModeBadge mode={q.mode} />
                  <p className="flex-1 truncate text-sm">{q.query_text}</p>
                  <span className="shrink-0 text-xs text-muted-foreground">
                    {formatDistanceToNow(new Date(q.created_at), {
                      addSuffix: true,
                    })}
                  </span>
                  <ArrowUpRight className="h-3.5 w-3.5 text-muted-foreground/50" />
                </a>
              </li>
            ))}
          </ul>
        </Card>
      )}
    </div>
  );
}
