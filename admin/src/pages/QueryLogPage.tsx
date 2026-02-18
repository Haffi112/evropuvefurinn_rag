import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { formatDistanceToNow } from "date-fns";
import { ChevronDown, ChevronRight, Eye, EyeOff, Search } from "lucide-react";
import { apiFetch } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

interface QueryLogEntry {
  id: number;
  query_text: string;
  response_text: string | null;
  model_used: string | null;
  references: Array<{ id: string; title: string }>;
  scope_declined: boolean;
  cached: boolean;
  latency_ms: number | null;
  ip_address: string | null;
  created_at: string;
  review_status: string;
}

interface QueryLogList {
  logs: QueryLogEntry[];
  total: number;
  page: number;
  per_page: number;
  total_pages: number;
}

interface QueryLogStats {
  total_queries: number;
  today_queries: number;
  cached_queries: number;
  declined_queries: number;
  avg_latency_ms: number;
}

export default function QueryLogPage() {
  const queryClient = useQueryClient();
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState("");
  const [cachedFilter, setCachedFilter] = useState<string>("all");
  const [modelFilter, setModelFilter] = useState<string>("all");
  const [expanded, setExpanded] = useState<Set<number>>(new Set());

  async function toggleExclusion(queryId: number, newStatus: string) {
    await apiFetch(`/api/v1/admin/query-log/${queryId}/review-status`, {
      method: "PATCH",
      body: JSON.stringify({ review_status: newStatus }),
    });
    queryClient.invalidateQueries({ queryKey: ["query-logs"] });
  }

  const params = new URLSearchParams();
  params.set("page", String(page));
  params.set("per_page", "30");
  if (search) params.set("search", search);
  if (cachedFilter === "yes") params.set("cached", "true");
  if (cachedFilter === "no") params.set("cached", "false");
  if (modelFilter !== "all") params.set("model_used", modelFilter);

  const logs = useQuery<QueryLogList>({
    queryKey: ["query-logs", page, search, cachedFilter, modelFilter],
    queryFn: () => apiFetch(`/api/v1/admin/query-log?${params}`),
  });

  const stats = useQuery<QueryLogStats>({
    queryKey: ["query-log-stats"],
    queryFn: () => apiFetch("/api/v1/admin/query-log/stats"),
    refetchInterval: 30_000,
  });

  function toggleExpand(id: number) {
    setExpanded((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  }

  const st = stats.data;

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold">Query Log</h1>

      {/* Stats bar */}
      {st && (
        <div className="grid gap-4 sm:grid-cols-4">
          <MiniStat label="Total" value={st.total_queries} />
          <MiniStat label="Today" value={st.today_queries} />
          <MiniStat label="Cached" value={st.cached_queries} />
          <MiniStat label="Avg Latency" value={`${st.avg_latency_ms}ms`} />
        </div>
      )}

      {/* Filters */}
      <div className="flex flex-wrap items-center gap-3">
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
        <Select
          value={cachedFilter}
          onValueChange={(v) => {
            setCachedFilter(v);
            setPage(1);
          }}
        >
          <SelectTrigger className="w-32">
            <SelectValue placeholder="Cache" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All</SelectItem>
            <SelectItem value="yes">Cached</SelectItem>
            <SelectItem value="no">Not Cached</SelectItem>
          </SelectContent>
        </Select>
        <Select
          value={modelFilter}
          onValueChange={(v) => {
            setModelFilter(v);
            setPage(1);
          }}
        >
          <SelectTrigger className="w-40">
            <SelectValue placeholder="Model" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All Models</SelectItem>
            <SelectItem value="gemini-3-pro">Pro</SelectItem>
            <SelectItem value="gemini-3-flash">Flash</SelectItem>
          </SelectContent>
        </Select>
      </div>

      {/* Table */}
      {logs.isLoading ? (
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
                <TableHead className="w-8" />
                <TableHead>Query</TableHead>
                <TableHead>Model</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Latency</TableHead>
                <TableHead>IP</TableHead>
                <TableHead>Time</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {logs.data?.logs.map((log) => (
                <>
                  <TableRow
                    key={log.id}
                    className="cursor-pointer"
                    onClick={() => toggleExpand(log.id)}
                  >
                    <TableCell>
                      {expanded.has(log.id) ? (
                        <ChevronDown className="h-4 w-4" />
                      ) : (
                        <ChevronRight className="h-4 w-4" />
                      )}
                    </TableCell>
                    <TableCell className="max-w-xs truncate font-mono text-xs">
                      {log.query_text}
                    </TableCell>
                    <TableCell>
                      <Badge variant="outline" className="text-xs">
                        {log.model_used ?? "—"}
                      </Badge>
                    </TableCell>
                    <TableCell className="space-x-1">
                      {log.review_status === "excluded" && (
                        <Badge variant="outline" className="text-xs text-muted-foreground">excluded</Badge>
                      )}
                      {log.cached && <Badge variant="secondary">cached</Badge>}
                      {log.scope_declined && (
                        <Badge variant="destructive">declined</Badge>
                      )}
                      {!log.cached && !log.scope_declined && log.review_status !== "excluded" && <Badge>live</Badge>}
                    </TableCell>
                    <TableCell className="text-xs text-muted-foreground">
                      {log.latency_ms != null ? `${log.latency_ms}ms` : "—"}
                    </TableCell>
                    <TableCell className="text-xs text-muted-foreground">
                      {log.ip_address ?? "—"}
                    </TableCell>
                    <TableCell className="text-xs text-muted-foreground">
                      {formatDistanceToNow(new Date(log.created_at), {
                        addSuffix: true,
                      })}
                    </TableCell>
                  </TableRow>
                  {expanded.has(log.id) && (
                    <TableRow key={`${log.id}-detail`}>
                      <TableCell colSpan={7} className="bg-muted/30 p-4">
                        <div className="space-y-3 text-sm">
                          <div className="flex items-center justify-between">
                            <p className="font-medium">Response:</p>
                            <div className="flex items-center gap-2">
                              {log.review_status !== "pending" && (
                                <Badge variant="outline" className="text-xs">
                                  {log.review_status}
                                </Badge>
                              )}
                              <Button
                                variant="outline"
                                size="sm"
                                onClick={(e) => {
                                  e.stopPropagation();
                                  toggleExclusion(
                                    log.id,
                                    log.review_status === "excluded" ? "pending" : "excluded",
                                  );
                                }}
                              >
                                {log.review_status === "excluded" ? (
                                  <>
                                    <Eye className="mr-1.5 h-3.5 w-3.5" />
                                    Include in review
                                  </>
                                ) : (
                                  <>
                                    <EyeOff className="mr-1.5 h-3.5 w-3.5" />
                                    Exclude from review
                                  </>
                                )}
                              </Button>
                            </div>
                          </div>
                          <p className="max-h-40 overflow-y-auto whitespace-pre-wrap text-muted-foreground">
                            {log.response_text ?? "No response"}
                          </p>
                          {log.references.length > 0 && (
                            <>
                              <p className="font-medium">
                                References ({log.references.length}):
                              </p>
                              <ul className="list-inside list-disc text-muted-foreground">
                                {log.references.map((ref) => (
                                  <li key={ref.id}>
                                    {ref.title} ({ref.id})
                                  </li>
                                ))}
                              </ul>
                            </>
                          )}
                        </div>
                      </TableCell>
                    </TableRow>
                  )}
                </>
              ))}
              {logs.data?.logs.length === 0 && (
                <TableRow>
                  <TableCell colSpan={7} className="text-center text-muted-foreground">
                    No logs found
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>

          {logs.data && logs.data.total_pages > 1 && (
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
                Page {logs.data.page} of {logs.data.total_pages}
              </span>
              <Button
                variant="outline"
                size="sm"
                disabled={page >= logs.data.total_pages}
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

function MiniStat({ label, value }: { label: string; value: string | number }) {
  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-xs font-medium text-muted-foreground">
          {label}
        </CardTitle>
      </CardHeader>
      <CardContent>
        <div className="text-xl font-bold">{value}</div>
      </CardContent>
    </Card>
  );
}
