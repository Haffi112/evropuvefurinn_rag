import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { formatDistanceToNow } from "date-fns";
import { ArrowRight, BookOpen, Globe } from "lucide-react";
import { reviewFetch } from "@review/lib/review-api";
import { useReviewAuth } from "@review/hooks/use-review-auth";
import {
  DailyBars,
  DonutChart,
  HorizontalBars,
  Metric,
  formatDuration,
} from "@/components/charts";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";

interface ReviewerStats {
  total: number;
  this_week: number;
  today: number;
  median_duration_seconds: number | null;
  avg_duration_seconds: number | null;
  total_duration_seconds: number;
  queue_remaining: number;
  total_in_scope: number;
  reviewed_by_me: number;
  daily_activity: { day: string; count: number }[];
  mode_split: { mode: string; count: number }[];
  checklist_pass_rates: {
    key: string;
    true_count: number;
    total: number;
    rate: number;
  }[];
  recent: {
    query_log_id: number;
    query_text: string;
    mode: string;
    review_status: string;
    duration_seconds: number | null;
    created_at: string;
  }[];
}

const CHECKLIST_LABELS: Record<string, string> = {
  answers_question: "Answers the question asked",
  factually_accurate: "Factually accurate",
  sources_relevant: "Sources relevant",
  no_hallucinations: "No hallucinations",
  appropriate_scope: "Appropriate scope",
  language_quality: "Language quality",
  publishable_minor_edits: "Publishable with minor edits",
};

export default function ReviewerStatsPage() {
  const { username } = useReviewAuth();
  const { data, isLoading } = useQuery<ReviewerStats>({
    queryKey: ["reviewer-stats"],
    queryFn: () => reviewFetch("/api/v1/review/stats"),
    staleTime: 30_000,
  });

  if (isLoading) {
    return (
      <div className="space-y-6">
        <Skeleton className="h-10 w-64" />
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-28" />
          ))}
        </div>
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }

  if (!data) return <p className="text-muted-foreground">Could not load stats.</p>;

  const ragCount = data.mode_split.find((m) => m.mode === "rag")?.count ?? 0;
  const webCount = data.mode_split.find((m) => m.mode === "websearch")?.count ?? 0;

  return (
    <div className="space-y-8">
      {/* Header */}
      <div>
        <p className="text-[10px] font-bold uppercase tracking-[0.12em] text-muted-foreground">
          Your review dashboard
        </p>
        <h1 className="mt-1 text-3xl font-bold">
          Hi{username ? `, ${username}` : ""}
        </h1>
        <p className="mt-1 text-sm text-muted-foreground">
          {data.total === 0
            ? "You haven't reviewed any queries yet — head to the queue and get started."
            : `You've reviewed ${data.total.toLocaleString()} ${data.total === 1 ? "query" : "queries"} so far.`}
        </p>
      </div>

      {/* Hero metrics */}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Metric
          label="All-time reviews"
          value={data.total.toLocaleString()}
          accent="primary"
        />
        <Metric
          label="This week"
          value={data.this_week.toLocaleString()}
          suffix={data.today > 0 ? `· ${data.today} today` : undefined}
          accent="emerald"
        />
        <Metric
          label="Median time"
          value={formatDuration(data.median_duration_seconds)}
          hint={
            data.avg_duration_seconds
              ? `avg ${formatDuration(data.avg_duration_seconds)}`
              : undefined
          }
          accent="sky"
        />
        <Metric
          label="Queue remaining"
          value={data.queue_remaining.toLocaleString()}
          hint={
            data.total_in_scope > 0
              ? `${data.reviewed_by_me} / ${data.total_in_scope} done`
              : "your queue"
          }
          accent="amber"
        />
      </div>

      {/* Activity + mode split */}
      <div className="grid gap-6 lg:grid-cols-[2fr_1fr]">
        <Card className="p-5">
          <div className="mb-4 flex items-baseline justify-between">
            <h2 className="text-sm font-bold uppercase tracking-wider text-muted-foreground">
              Daily activity
            </h2>
            <span className="text-xs text-muted-foreground">last 30 days</span>
          </div>
          <DailyBars data={data.daily_activity} unit="reviews" />
        </Card>

        <Card className="p-5">
          <h2 className="mb-4 text-sm font-bold uppercase tracking-wider text-muted-foreground">
            By source
          </h2>
          <DonutChart
            slices={[
              { label: "RAG", value: ragCount, color: "rgb(245 158 11)" },
              { label: "Web", value: webCount, color: "rgb(14 165 233)" },
            ]}
            centerValue={data.total}
            centerLabel={`${data.total} total reviews`}
          />
        </Card>
      </div>

      {/* Checklist pass rates */}
      <Card className="p-5">
        <div className="mb-4 flex items-baseline justify-between">
          <h2 className="text-sm font-bold uppercase tracking-wider text-muted-foreground">
            Your checklist pass rates
          </h2>
          <span className="text-xs text-muted-foreground">
            % of reviews where you marked each item true
          </span>
        </div>
        {data.checklist_pass_rates.length === 0 ? (
          <p className="text-sm text-muted-foreground">No data yet.</p>
        ) : (
          <HorizontalBars
            rows={data.checklist_pass_rates.map((c) => ({
              label: CHECKLIST_LABELS[c.key] ?? c.key,
              value: c.rate,
              maxValue: 100,
              secondary: `${c.true_count}/${c.total} (${c.rate.toFixed(0)}%)`,
            }))}
          />
        )}
      </Card>

      {/* Recent reviews */}
      <Card className="p-5">
        <div className="mb-4 flex items-baseline justify-between">
          <h2 className="text-sm font-bold uppercase tracking-wider text-muted-foreground">
            Recent reviews
          </h2>
          <Link
            to="/queries?mine_filter=done"
            className="text-xs text-primary hover:underline"
          >
            View all →
          </Link>
        </div>
        {data.recent.length === 0 ? (
          <p className="text-sm text-muted-foreground">No reviews yet.</p>
        ) : (
          <ul className="divide-y">
            {data.recent.map((r) => {
              const Icon = r.mode === "websearch" ? Globe : BookOpen;
              const iconColor =
                r.mode === "websearch" ? "text-sky-600" : "text-amber-600";
              return (
                <li key={r.query_log_id}>
                  <Link
                    to={`/queries/${r.query_log_id}`}
                    className="flex items-center gap-3 py-3 transition-colors hover:bg-muted/40"
                  >
                    <Icon className={`h-4 w-4 shrink-0 ${iconColor}`} />
                    <p className="flex-1 truncate text-sm">
                      {r.query_text}
                    </p>
                    <span className="shrink-0 text-xs tabular-nums text-muted-foreground">
                      {formatDuration(r.duration_seconds)}
                    </span>
                    <span className="shrink-0 text-xs text-muted-foreground">
                      {formatDistanceToNow(new Date(r.created_at), {
                        addSuffix: true,
                      })}
                    </span>
                    <ArrowRight className="h-3.5 w-3.5 text-muted-foreground/50" />
                  </Link>
                </li>
              );
            })}
          </ul>
        )}
      </Card>

      {/* Total time spent footer */}
      {data.total_duration_seconds > 0 && (
        <p className="text-center text-xs text-muted-foreground">
          Lifetime time spent reviewing:{" "}
          <span className="font-medium text-foreground tabular-nums">
            {formatDuration(data.total_duration_seconds)}
          </span>
        </p>
      )}
    </div>
  );
}
