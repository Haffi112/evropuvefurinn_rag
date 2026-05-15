import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { formatDistanceToNow } from "date-fns";
import { CheckCircle2, CircleDashed, Search } from "lucide-react";
import { reviewFetch } from "@review/lib/review-api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import ModeBadge from "@/components/ModeBadge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

interface ReviewQueryItem {
  id: number;
  query_text: string;
  model_used: string | null;
  // Server sanitizes this to a per-reviewer label: 'pending' | 'done'.
  review_status: string;
  cached: boolean;
  created_at: string;
  i_evaluated: boolean;
  mode: "rag" | "websearch";
}

interface ReviewQueryList {
  queries: ReviewQueryItem[];
  total: number;
  page: number;
  per_page: number;
  total_pages: number;
}

interface ReviewerStats {
  total: number;
  queue_remaining: number;
  total_in_scope: number;
  reviewed_by_me: number;
}

type MineFilter = "pending" | "done";

const TABS: { value: MineFilter; label: string }[] = [
  { value: "pending", label: "To do" },
  { value: "done", label: "Done" },
];

export default function ReviewListPage() {
  const navigate = useNavigate();
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState("");
  const [mineFilter, setMineFilter] = useState<MineFilter>("pending");

  const params = new URLSearchParams();
  params.set("page", String(page));
  params.set("per_page", "30");
  params.set("mine_filter", mineFilter);
  if (search) params.set("search", search);

  const { data, isLoading } = useQuery<ReviewQueryList>({
    queryKey: ["review-queries", page, search, mineFilter],
    queryFn: () => reviewFetch(`/api/v1/review/queries?${params}`),
  });

  // Personal progress — pulled from /review/stats which now returns
  // per-reviewer queue progress (reviewed_by_me, queue_remaining,
  // total_in_scope).
  const stats = useQuery<ReviewerStats>({
    queryKey: ["reviewer-stats-queue"],
    queryFn: () => reviewFetch("/api/v1/review/stats"),
    staleTime: 15_000,
    refetchInterval: 30_000,
  });

  const reviewed = stats.data?.reviewed_by_me ?? 0;
  const totalInScope = stats.data?.total_in_scope ?? 0;
  const remaining = stats.data?.queue_remaining ?? 0;
  const progressPct =
    totalInScope > 0 ? Math.min(100, Math.round((100 * reviewed) / totalInScope)) : 0;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-bold">Your queue</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          {remaining > 0
            ? `${remaining.toLocaleString()} ${remaining === 1 ? "query" : "queries"} left for you to review.`
            : "You're all caught up — nothing left to review."}
        </p>
      </div>

      {/* Personal progress */}
      <Card className="card-accent">
        <CardHeader className="pb-3">
          <CardTitle className="text-xs font-bold uppercase tracking-wider text-muted-foreground">
            Your progress
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex items-baseline justify-between">
            <div className="text-2xl font-bold tabular-nums text-primary">
              {stats.isLoading ? (
                <Skeleton className="h-7 w-32" />
              ) : (
                <>
                  {reviewed.toLocaleString()}
                  <span className="ml-1 text-base font-normal text-muted-foreground">
                    / {totalInScope.toLocaleString()} reviewed
                  </span>
                </>
              )}
            </div>
            <span className="text-sm font-semibold tabular-nums text-muted-foreground">
              {progressPct}%
            </span>
          </div>
          {/* Progress bar */}
          <div
            className="h-2 w-full overflow-hidden rounded-full bg-muted"
            role="progressbar"
            aria-valuenow={progressPct}
            aria-valuemin={0}
            aria-valuemax={100}
          >
            <div
              className="h-full bg-primary transition-all duration-500"
              style={{ width: `${progressPct}%` }}
            />
          </div>
        </CardContent>
      </Card>

      {/* Personal mini-stats */}
      <div className="grid gap-4 sm:grid-cols-3">
        <MiniStat
          label="Reviewed by you"
          value={stats.isLoading ? "..." : reviewed.toLocaleString()}
          icon={<CheckCircle2 className="h-4 w-4 text-emerald-500" />}
        />
        <MiniStat
          label="Remaining for you"
          value={stats.isLoading ? "..." : remaining.toLocaleString()}
          icon={<CircleDashed className="h-4 w-4 text-amber-500" />}
        />
        <MiniStat
          label="Total in scope"
          value={stats.isLoading ? "..." : totalInScope.toLocaleString()}
        />
      </div>

      {/* Filter tabs + search */}
      <div className="flex flex-wrap items-center gap-3">
        <div className="flex gap-1 rounded-sm border bg-muted p-1">
          {TABS.map((tab) => (
            <button
              key={tab.value}
              onClick={() => {
                setMineFilter(tab.value);
                setPage(1);
              }}
              className={`rounded-sm px-3 py-1.5 text-sm font-medium transition-colors ${
                mineFilter === tab.value
                  ? "bg-background text-primary shadow-sm"
                  : "text-muted-foreground hover:text-foreground"
              }`}
            >
              {tab.label}
            </button>
          ))}
        </div>
        <div className="relative max-w-xs flex-1">
          <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            placeholder="Search queries..."
            value={search}
            onChange={(e) => {
              setSearch(e.target.value);
              setPage(1);
            }}
            className="pl-10"
          />
        </div>
      </div>

      {/* Table */}
      {isLoading ? (
        <div className="space-y-2">
          {Array.from({ length: 10 }).map((_, i) => (
            <Skeleton key={i} className="h-10 w-full" />
          ))}
        </div>
      ) : (
        <>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Query</TableHead>
                <TableHead className="w-24">Source</TableHead>
                <TableHead>Model</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Date</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {data?.queries.map((q) => (
                <TableRow
                  key={q.id}
                  className="cursor-pointer"
                  onClick={() => navigate(`/queries/${q.id}`)}
                >
                  <TableCell className="max-w-sm truncate font-mono text-xs">
                    {q.query_text}
                  </TableCell>
                  <TableCell>
                    <ModeBadge mode={q.mode} />
                  </TableCell>
                  <TableCell>
                    <Badge variant="outline" className="text-xs">
                      {q.model_used ?? "—"}
                    </Badge>
                  </TableCell>
                  <TableCell>
                    {q.i_evaluated ? (
                      <Badge variant="default" className="gap-1 text-xs">
                        <CheckCircle2 className="h-3 w-3" />
                        Done by you
                      </Badge>
                    ) : (
                      <Badge variant="outline" className="gap-1 text-xs">
                        <CircleDashed className="h-3 w-3" />
                        To review
                      </Badge>
                    )}
                  </TableCell>
                  <TableCell className="text-xs text-muted-foreground">
                    {formatDistanceToNow(new Date(q.created_at), {
                      addSuffix: true,
                    })}
                  </TableCell>
                </TableRow>
              ))}
              {data?.queries.length === 0 && (
                <TableRow>
                  <TableCell
                    colSpan={5}
                    className="py-8 text-center text-muted-foreground"
                  >
                    {mineFilter === "pending"
                      ? "Nothing left for you to review — great work."
                      : "You haven't completed any reviews yet."}
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>

          {data && data.total_pages > 1 && (
            <div className="flex items-center justify-center gap-2">
              <Button
                variant="outline"
                size="sm"
                disabled={page <= 1}
                onClick={() => setPage((p) => p - 1)}
              >
                Previous
              </Button>
              <span className="text-sm text-muted-foreground">
                Page {data.page} of {data.total_pages}
              </span>
              <Button
                variant="outline"
                size="sm"
                disabled={page >= data.total_pages}
                onClick={() => setPage((p) => p + 1)}
              >
                Next
              </Button>
            </div>
          )}
        </>
      )}
    </div>
  );
}

function MiniStat({
  label,
  value,
  icon,
}: {
  label: string;
  value: string | number;
  icon?: React.ReactNode;
}) {
  return (
    <Card className="card-accent">
      <CardHeader className="pb-2">
        <CardTitle className="flex items-center gap-2 text-xs font-bold uppercase tracking-wider text-muted-foreground">
          {icon}
          {label}
        </CardTitle>
      </CardHeader>
      <CardContent>
        <div className="text-xl font-bold tabular-nums text-primary">{value}</div>
      </CardContent>
    </Card>
  );
}
