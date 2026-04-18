import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Plus } from "lucide-react";
import { apiFetch } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

interface BatchListItem {
  id: number;
  filename: string;
  total: number;
  status: string;
  done: number;
  failed: number;
  pending: number;
  processing: number;
  cancelled: number;
  created_at: string;
  completed_at: string | null;
}

function StatusBadge({ status }: { status: string }) {
  const variant = status === "running" ? "default" : status === "completed" ? "secondary" : "destructive";
  return <Badge variant={variant}>{status}</Badge>;
}

function ProgressBar({ done, failed, cancelled, total }: { done: number; failed: number; cancelled: number; total: number }) {
  const finished = done + failed + cancelled;
  const pct = total > 0 ? Math.round((finished / total) * 100) : 0;
  return (
    <div className="w-40">
      <div className="h-2 w-full overflow-hidden rounded-full bg-muted">
        <div
          className="h-full bg-primary transition-all"
          style={{ width: `${pct}%` }}
        />
      </div>
      <div className="mt-0.5 flex justify-between text-xs text-muted-foreground tabular-nums">
        <span>{finished} / {total}</span>
        <span>{pct}%</span>
      </div>
    </div>
  );
}

export default function BatchesListPage() {
  const [batches, setBatches] = useState<BatchListItem[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    const fetchData = async () => {
      try {
        const data = await apiFetch<BatchListItem[]>("/api/v1/admin/batches");
        if (!cancelled) setBatches(data);
      } catch (e) {
        console.error(e);
      } finally {
        if (!cancelled) setLoading(false);
      }
    };

    fetchData();
    const anyRunning = () => batches.some((b) => b.status === "running");
    const interval = setInterval(() => {
      if (anyRunning()) fetchData();
    }, 10_000);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-3xl font-bold">Batches</h1>
        <Link to="/batches/new">
          <Button size="sm">
            <Plus className="mr-2 h-4 w-4" />
            New batch
          </Button>
        </Link>
      </div>

      {loading ? (
        <p className="text-sm text-muted-foreground">Loading…</p>
      ) : batches.length === 0 ? (
        <Card className="p-6 text-center text-sm text-muted-foreground">
          No batches yet. <Link to="/batches/new" className="text-primary">Upload one</Link> to get started.
        </Card>
      ) : (
        <Card>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>#</TableHead>
                <TableHead>Filename</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Progress</TableHead>
                <TableHead>Failed</TableHead>
                <TableHead>Created</TableHead>
                <TableHead />
              </TableRow>
            </TableHeader>
            <TableBody>
              {batches.map((b) => (
                <TableRow key={b.id}>
                  <TableCell className="font-mono text-xs">{b.id}</TableCell>
                  <TableCell className="max-w-xs truncate">{b.filename}</TableCell>
                  <TableCell><StatusBadge status={b.status} /></TableCell>
                  <TableCell>
                    <ProgressBar
                      done={b.done}
                      failed={b.failed}
                      cancelled={b.cancelled}
                      total={b.total}
                    />
                  </TableCell>
                  <TableCell>
                    {b.failed > 0 ? (
                      <span className="text-destructive">{b.failed}</span>
                    ) : (
                      <span className="text-muted-foreground">0</span>
                    )}
                  </TableCell>
                  <TableCell className="text-xs text-muted-foreground">
                    {new Date(b.created_at).toLocaleString()}
                  </TableCell>
                  <TableCell>
                    <Link to={`/batches/${b.id}`} className="text-sm text-primary">
                      View
                    </Link>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Card>
      )}
    </div>
  );
}
