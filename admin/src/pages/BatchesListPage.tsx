import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Layers, Plus, Trash2, Upload } from "lucide-react";
import { apiFetch } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
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
  const [deleteTarget, setDeleteTarget] = useState<BatchListItem | null>(null);
  const [deleting, setDeleting] = useState(false);

  const fetchData = async () => {
    try {
      const data = await apiFetch<BatchListItem[]>("/api/v1/admin/batches");
      setBatches(data);
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchData();
    const anyRunning = () => batches.some((b) => b.status === "running");
    const interval = setInterval(() => {
      if (anyRunning()) fetchData();
    }, 10_000);
    return () => clearInterval(interval);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleDelete = async () => {
    if (!deleteTarget) return;
    setDeleting(true);
    try {
      await apiFetch(`/api/v1/admin/batches/${deleteTarget.id}`, { method: "DELETE" });
      setDeleteTarget(null);
      await fetchData();
    } catch (e) {
      console.error(e);
    } finally {
      setDeleting(false);
    }
  };

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
        <Card className="flex flex-col items-center justify-center gap-5 border-dashed py-16 text-center">
          <div className="flex h-14 w-14 items-center justify-center rounded-full bg-primary/10">
            <Layers className="h-7 w-7 text-primary" />
          </div>
          <div className="space-y-1.5">
            <h3 className="text-lg font-semibold">No batches yet</h3>
            <p className="mx-auto max-w-sm text-sm text-muted-foreground">
              Upload a JSONL file of questions to answer them in bulk via RAG and web search.
              Each question becomes two review items.
            </p>
          </div>
          <Link to="/batches/new">
            <Button size="lg">
              <Upload className="mr-2 h-4 w-4" />
              Upload your first batch
            </Button>
          </Link>
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
                  <TableCell className="text-right">
                    <div className="flex items-center justify-end gap-1">
                      <Link to={`/batches/${b.id}`} className="text-sm text-primary hover:underline">
                        View
                      </Link>
                      <Button
                        variant="ghost"
                        size="sm"
                        className="h-8 w-8 p-0 text-muted-foreground hover:text-destructive"
                        onClick={(e) => {
                          e.preventDefault();
                          e.stopPropagation();
                          setDeleteTarget(b);
                        }}
                        title="Delete batch"
                      >
                        <Trash2 className="h-4 w-4" />
                      </Button>
                    </div>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Card>
      )}

      <Dialog open={!!deleteTarget} onOpenChange={(open) => !open && setDeleteTarget(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Delete this batch?</DialogTitle>
            <DialogDescription asChild>
              <div className="space-y-2">
                <p>
                  <span className="font-mono text-xs">{deleteTarget?.filename}</span>
                  {" — "}
                  this removes the batch grouping and all {deleteTarget?.total} queue items.
                </p>
                <p className="text-sm">
                  Answered queries, reviewer evaluations, and article drafts{" "}
                  <span className="font-medium text-foreground">are preserved</span>{" "}
                  — they'll remain in the reviewer queue attributed to "batch".
                </p>
              </div>
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDeleteTarget(null)} disabled={deleting}>
              Cancel
            </Button>
            <Button variant="destructive" onClick={handleDelete} disabled={deleting}>
              {deleting ? "Deleting…" : "Delete batch"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
