import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { formatDistanceToNow } from "date-fns";
import { Search } from "lucide-react";
import { reviewFetch } from "@review/lib/review-api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
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
  review_status: string;
  cached: boolean;
  created_at: string;
  reviewer_username: string | null;
}

interface ReviewQueryList {
  queries: ReviewQueryItem[];
  total: number;
  page: number;
  per_page: number;
  total_pages: number;
}

const STATUS_TABS = ["all", "pending", "reviewed", "approved"] as const;

function statusBadgeVariant(status: string) {
  switch (status) {
    case "approved":
      return "default" as const;
    case "reviewed":
      return "secondary" as const;
    default:
      return "outline" as const;
  }
}

export default function ReviewListPage() {
  const navigate = useNavigate();
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState<string>("all");

  const params = new URLSearchParams();
  params.set("page", String(page));
  params.set("per_page", "30");
  if (search) params.set("search", search);
  if (statusFilter !== "all") params.set("review_status", statusFilter);

  const { data, isLoading } = useQuery<ReviewQueryList>({
    queryKey: ["review-queries", page, search, statusFilter],
    queryFn: () => reviewFetch(`/api/v1/review/queries?${params}`),
  });

  // Fetch all to compute counts
  const allQuery = useQuery<ReviewQueryList>({
    queryKey: ["review-queries-stats"],
    queryFn: () => reviewFetch("/api/v1/review/queries?per_page=1"),
    staleTime: 60_000,
  });
  const pendingQuery = useQuery<ReviewQueryList>({
    queryKey: ["review-queries-stats-pending"],
    queryFn: () =>
      reviewFetch("/api/v1/review/queries?per_page=1&review_status=pending"),
    staleTime: 60_000,
  });
  const reviewedQuery = useQuery<ReviewQueryList>({
    queryKey: ["review-queries-stats-reviewed"],
    queryFn: () =>
      reviewFetch("/api/v1/review/queries?per_page=1&review_status=reviewed"),
    staleTime: 60_000,
  });
  const approvedQuery = useQuery<ReviewQueryList>({
    queryKey: ["review-queries-stats-approved"],
    queryFn: () =>
      reviewFetch("/api/v1/review/queries?per_page=1&review_status=approved"),
    staleTime: 60_000,
  });

  return (
    <div className="space-y-6">
      <h1 className="text-3xl font-bold">Queries</h1>

      {/* Stats bar */}
      <div className="grid gap-4 sm:grid-cols-4">
        <MiniStat label="Total" value={allQuery.data?.total ?? "..."} />
        <MiniStat label="Pending" value={pendingQuery.data?.total ?? "..."} />
        <MiniStat label="Reviewed" value={reviewedQuery.data?.total ?? "..."} />
        <MiniStat label="Approved" value={approvedQuery.data?.total ?? "..."} />
      </div>

      {/* Filter tabs + search */}
      <div className="flex flex-wrap items-center gap-3">
        <div className="flex gap-1 rounded-sm border bg-muted p-1">
          {STATUS_TABS.map((tab) => (
            <button
              key={tab}
              onClick={() => {
                setStatusFilter(tab);
                setPage(1);
              }}
              className={`rounded-sm px-3 py-1.5 text-sm font-medium transition-colors ${
                statusFilter === tab
                  ? "bg-background text-primary shadow-sm"
                  : "text-muted-foreground hover:text-foreground"
              }`}
            >
              {tab.charAt(0).toUpperCase() + tab.slice(1)}
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
                <TableHead>Model</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Reviewed by</TableHead>
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
                    <Badge variant="outline" className="text-xs">
                      {q.model_used ?? "\u2014"}
                    </Badge>
                  </TableCell>
                  <TableCell>
                    <Badge variant={statusBadgeVariant(q.review_status)}>
                      {q.review_status}
                    </Badge>
                  </TableCell>
                  <TableCell className="text-sm text-muted-foreground">
                    {q.reviewer_username ?? "\u2014"}
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
                    className="text-center text-muted-foreground"
                  >
                    No queries found
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
}: {
  label: string;
  value: string | number;
}) {
  return (
    <Card className="card-accent">
      <CardHeader className="pb-2">
        <CardTitle className="text-xs font-bold uppercase tracking-wider text-muted-foreground">
          {label}
        </CardTitle>
      </CardHeader>
      <CardContent>
        <div className="text-xl font-bold text-primary">{value}</div>
      </CardContent>
    </Card>
  );
}
