import { useQuery } from "@tanstack/react-query";
import { FileText, Zap, Database, ShieldAlert } from "lucide-react";
import { formatDistanceToNow } from "date-fns";
import { apiFetch } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

interface Stats {
  articles: { total: number; last_synced: string | null };
  queries: { today: number; cache_hit_rate: number };
  quota: Record<string, { used: number; limit: number | null; resets_at?: string }>;
  vectors: Record<string, unknown>;
}

interface HealthData {
  status: string;
  version: string;
  checks: Record<string, string>;
}

interface QueryLogEntry {
  id: number;
  query_text: string;
  model_used: string | null;
  scope_declined: boolean;
  cached: boolean;
  latency_ms: number | null;
  created_at: string;
}

interface QueryLogList {
  logs: QueryLogEntry[];
  total: number;
}

export default function DashboardPage() {
  const stats = useQuery<Stats>({
    queryKey: ["stats"],
    queryFn: () => apiFetch("/api/v1/stats"),
    refetchInterval: 30_000,
  });

  const health = useQuery<HealthData>({
    queryKey: ["health"],
    queryFn: () => apiFetch("/api/v1/health"),
    refetchInterval: 30_000,
  });

  const recentQueries = useQuery<QueryLogList>({
    queryKey: ["recent-queries"],
    queryFn: () => apiFetch("/api/v1/admin/query-log?per_page=10"),
    refetchInterval: 15_000,
  });

  const s = stats.data;

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold">Dashboard</h1>

      {/* Stat cards */}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard
          title="Total Articles"
          value={s?.articles.total}
          icon={<FileText className="h-4 w-4 text-muted-foreground" />}
          loading={stats.isLoading}
        />
        <StatCard
          title="Queries Today"
          value={s?.queries.today}
          icon={<Zap className="h-4 w-4 text-muted-foreground" />}
          loading={stats.isLoading}
        />
        <StatCard
          title="Pro Quota Used"
          value={
            s
              ? `${s.quota.gemini_3_pro?.used ?? 0} / ${s.quota.gemini_3_pro?.limit ?? "∞"}`
              : undefined
          }
          icon={<Database className="h-4 w-4 text-muted-foreground" />}
          loading={stats.isLoading}
        />
        <StatCard
          title="System Status"
          value={health.data?.status === "healthy" ? "Healthy" : "Degraded"}
          icon={<ShieldAlert className="h-4 w-4 text-muted-foreground" />}
          loading={health.isLoading}
        />
      </div>

      {/* Health checks */}
      {health.data && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Service Health</CardTitle>
          </CardHeader>
          <CardContent className="flex flex-wrap gap-2">
            {Object.entries(health.data.checks).map(([svc, status]) => (
              <Badge
                key={svc}
                variant={status === "connected" || status === "available" ? "default" : "destructive"}
              >
                {svc}: {status}
              </Badge>
            ))}
            <Badge variant="secondary">v{health.data.version}</Badge>
          </CardContent>
        </Card>
      )}

      {/* Recent queries */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Recent Queries</CardTitle>
        </CardHeader>
        <CardContent>
          {recentQueries.isLoading ? (
            <div className="space-y-2">
              {Array.from({ length: 5 }).map((_, i) => (
                <Skeleton key={i} className="h-8 w-full" />
              ))}
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Query</TableHead>
                  <TableHead>Model</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Latency</TableHead>
                  <TableHead>Time</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {recentQueries.data?.logs.map((log) => (
                  <TableRow key={log.id}>
                    <TableCell className="max-w-xs truncate font-mono text-xs">
                      {log.query_text}
                    </TableCell>
                    <TableCell>
                      <Badge variant="outline" className="text-xs">
                        {log.model_used ?? "—"}
                      </Badge>
                    </TableCell>
                    <TableCell>
                      {log.cached && <Badge variant="secondary">cached</Badge>}
                      {log.scope_declined && (
                        <Badge variant="destructive">declined</Badge>
                      )}
                      {!log.cached && !log.scope_declined && (
                        <Badge>live</Badge>
                      )}
                    </TableCell>
                    <TableCell className="text-xs text-muted-foreground">
                      {log.latency_ms != null ? `${log.latency_ms}ms` : "—"}
                    </TableCell>
                    <TableCell className="text-xs text-muted-foreground">
                      {formatDistanceToNow(new Date(log.created_at), {
                        addSuffix: true,
                      })}
                    </TableCell>
                  </TableRow>
                ))}
                {recentQueries.data?.logs.length === 0 && (
                  <TableRow>
                    <TableCell colSpan={5} className="text-center text-muted-foreground">
                      No queries logged yet
                    </TableCell>
                  </TableRow>
                )}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

function StatCard({
  title,
  value,
  icon,
  loading,
}: {
  title: string;
  value: string | number | undefined;
  icon: React.ReactNode;
  loading: boolean;
}) {
  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between pb-2">
        <CardTitle className="text-sm font-medium">{title}</CardTitle>
        {icon}
      </CardHeader>
      <CardContent>
        {loading ? (
          <Skeleton className="h-7 w-20" />
        ) : (
          <div className="text-2xl font-bold">{value ?? "—"}</div>
        )}
      </CardContent>
    </Card>
  );
}
