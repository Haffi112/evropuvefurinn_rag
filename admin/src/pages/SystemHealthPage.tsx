import { useQuery } from "@tanstack/react-query";
import { apiFetch } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Separator } from "@/components/ui/separator";

interface HealthData {
  status: string;
  version: string;
  checks: Record<string, string>;
}

interface Stats {
  articles: { total: number; last_synced: string | null };
  queries: { today: number; cache_hit_rate: number };
  quota: Record<string, { used: number; limit: number | null; resets_at?: string }>;
  vectors: Record<string, unknown>;
}

export default function SystemHealthPage() {
  const health = useQuery<HealthData>({
    queryKey: ["health"],
    queryFn: () => apiFetch("/api/v1/health"),
    refetchInterval: 15_000,
  });

  const stats = useQuery<Stats>({
    queryKey: ["stats"],
    queryFn: () => apiFetch("/api/v1/stats"),
    refetchInterval: 30_000,
  });

  const h = health.data;
  const s = stats.data;

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold">System Health</h1>

      {/* Overall status */}
      {health.isLoading ? (
        <Skeleton className="h-24 w-full" />
      ) : h ? (
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-3 text-base">
              Overall Status
              <Badge
                variant={h.status === "healthy" ? "default" : "destructive"}
                className="text-sm"
              >
                {h.status}
              </Badge>
              <Badge variant="secondary">v{h.version}</Badge>
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="grid gap-3 sm:grid-cols-3">
              {Object.entries(h.checks).map(([svc, status]) => (
                <div
                  key={svc}
                  className="flex items-center justify-between rounded-md border p-3"
                >
                  <span className="text-sm font-medium capitalize">{svc}</span>
                  <Badge
                    variant={
                      status === "connected" || status === "available"
                        ? "default"
                        : "destructive"
                    }
                  >
                    {status}
                  </Badge>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      ) : null}

      {/* Quota */}
      {stats.isLoading ? (
        <Skeleton className="h-32 w-full" />
      ) : s ? (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Daily Quota Usage</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            {Object.entries(s.quota).map(([model, q]) => (
              <div key={model}>
                <div className="mb-1 flex items-center justify-between text-sm">
                  <span className="font-medium">{model}</span>
                  <span className="text-muted-foreground">
                    {q.used} / {q.limit ?? "unlimited"}
                  </span>
                </div>
                {q.limit ? (
                  <div className="h-2 overflow-hidden rounded-full bg-muted">
                    <div
                      className="h-full rounded-full bg-primary transition-all"
                      style={{ width: `${Math.min((q.used / q.limit) * 100, 100)}%` }}
                    />
                  </div>
                ) : (
                  <div className="h-2 overflow-hidden rounded-full bg-muted">
                    <div className="h-full w-full rounded-full bg-green-500/30" />
                  </div>
                )}
                {q.resets_at && (
                  <p className="mt-1 text-xs text-muted-foreground">
                    Resets at {new Date(q.resets_at).toLocaleTimeString()}
                  </p>
                )}
              </div>
            ))}
          </CardContent>
        </Card>
      ) : null}

      <Separator />

      {/* Cache & Vector stats */}
      {s && (
        <div className="grid gap-4 sm:grid-cols-2">
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Query Cache</CardTitle>
            </CardHeader>
            <CardContent className="space-y-2 text-sm">
              <div className="flex justify-between">
                <span className="text-muted-foreground">Queries Today</span>
                <span className="font-medium">{s.queries.today}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-muted-foreground">Cache Hit Rate</span>
                <span className="font-medium">
                  {(s.queries.cache_hit_rate * 100).toFixed(0)}%
                </span>
              </div>
            </CardContent>
          </Card>
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Vector Index</CardTitle>
            </CardHeader>
            <CardContent className="space-y-2 text-sm">
              {Object.entries(s.vectors).map(([key, val]) => (
                <div key={key} className="flex justify-between">
                  <span className="text-muted-foreground">{key}</span>
                  <span className="font-medium">{String(val)}</span>
                </div>
              ))}
            </CardContent>
          </Card>
        </div>
      )}
    </div>
  );
}
