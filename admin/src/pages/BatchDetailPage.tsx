import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { ArrowLeft, RotateCw, Trash2, X } from "lucide-react";
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

interface BatchItem {
  id: number;
  batch_id: number;
  question_id: string;
  question_text: string;
  mode: string;  // 'rag' | 'websearch'
  status: string;
  query_log_id: number | null;
  error: string | null;
  retry_count: number;
}

interface BatchDetail {
  id: number;
  filename: string;
  total: number;
  status: string;
  created_at: string;
  completed_at: string | null;
  items: BatchItem[];
}

interface GroupedRow {
  question_id: string;
  question_text: string;
  rag: BatchItem | undefined;
  websearch: BatchItem | undefined;
}

function statusVariant(status: string): "default" | "secondary" | "destructive" | "outline" {
  if (status === "done") return "secondary";
  if (status === "failed") return "destructive";
  if (status === "processing") return "default";
  return "outline";
}

function ItemCell({ item, onRetry }: { item: BatchItem | undefined; onRetry: (id: number) => void }) {
  if (!item) return <span className="text-xs text-muted-foreground">—</span>;
  if (item.status === "done" && item.query_log_id) {
    return (
      <a
        href={`/review/queries/${item.query_log_id}`}
        target="_blank"
        rel="noopener noreferrer"
        className="text-sm text-primary hover:underline"
      >
        <Badge variant="secondary">done</Badge>
      </a>
    );
  }
  if (item.status === "failed") {
    return (
      <div className="flex items-center gap-1">
        <Badge variant="destructive" title={item.error ?? undefined}>
          failed
        </Badge>
        <Button
          variant="ghost"
          size="sm"
          className="h-6 px-1 text-xs"
          onClick={() => onRetry(item.id)}
          title={item.error ?? "Retry"}
        >
          <RotateCw className="h-3 w-3" />
        </Button>
      </div>
    );
  }
  return <Badge variant={statusVariant(item.status)}>{item.status}</Badge>;
}

export default function BatchDetailPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [data, setData] = useState<BatchDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [actionLoading, setActionLoading] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);

  const fetchBatch = useCallback(async () => {
    if (!id) return;
    try {
      const res = await apiFetch<BatchDetail>(`/api/v1/admin/batches/${id}`);
      setData(res);
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  }, [id]);

  useEffect(() => {
    fetchBatch();
    const interval = setInterval(() => {
      if (data?.status === "running") fetchBatch();
    }, 3_000);
    return () => clearInterval(interval);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id, data?.status]);

  const handleRetryFailed = async () => {
    if (!id) return;
    setActionLoading(true);
    try {
      await apiFetch(`/api/v1/admin/batches/${id}/retry-failed`, { method: "POST" });
      await fetchBatch();
    } finally {
      setActionLoading(false);
    }
  };

  const handleRetryItem = async (itemId: number) => {
    if (!id) return;
    try {
      await apiFetch(`/api/v1/admin/batches/${id}/items/${itemId}/retry`, { method: "POST" });
      await fetchBatch();
    } catch (e) {
      console.error(e);
    }
  };

  const handleCancel = async () => {
    if (!id) return;
    if (!confirm("Cancel remaining pending items?")) return;
    setActionLoading(true);
    try {
      await apiFetch(`/api/v1/admin/batches/${id}/cancel`, { method: "POST" });
      await fetchBatch();
    } finally {
      setActionLoading(false);
    }
  };

  const handleDelete = async () => {
    if (!id) return;
    setActionLoading(true);
    try {
      await apiFetch(`/api/v1/admin/batches/${id}`, { method: "DELETE" });
      navigate("/batches");
    } catch (e) {
      console.error(e);
      setActionLoading(false);
      setDeleteOpen(false);
    }
  };

  if (loading) return <p className="text-sm text-muted-foreground">Loading…</p>;
  if (!data) return <p className="text-sm text-destructive">Batch not found.</p>;

  const done = data.items.filter((i) => i.status === "done").length;
  const failed = data.items.filter((i) => i.status === "failed").length;
  const cancelled = data.items.filter((i) => i.status === "cancelled").length;
  const finished = done + failed + cancelled;
  const pct = data.total > 0 ? Math.round((finished / data.total) * 100) : 0;

  // Group items by question_id, with rag/websearch columns
  const grouped: GroupedRow[] = [];
  const seen = new Map<string, GroupedRow>();
  for (const item of data.items) {
    let row = seen.get(item.question_id);
    if (!row) {
      row = {
        question_id: item.question_id,
        question_text: item.question_text,
        rag: undefined,
        websearch: undefined,
      };
      seen.set(item.question_id, row);
      grouped.push(row);
    }
    if (item.mode === "rag") row.rag = item;
    else if (item.mode === "websearch") row.websearch = item;
  }

  return (
    <div className="space-y-6">
      <div>
        <Link to="/batches" className="inline-flex items-center text-sm text-muted-foreground hover:text-foreground">
          <ArrowLeft className="mr-1 h-4 w-4" />
          Back to batches
        </Link>
      </div>

      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-3xl font-bold">{data.filename}</h1>
          <p className="text-sm text-muted-foreground">
            Batch #{data.id} · {data.status} · created {new Date(data.created_at).toLocaleString()}
          </p>
        </div>
        <div className="flex gap-2">
          {failed > 0 && (
            <Button variant="outline" size="sm" onClick={handleRetryFailed} disabled={actionLoading}>
              <RotateCw className="mr-2 h-4 w-4" />
              Retry all failed ({failed})
            </Button>
          )}
          {data.status === "running" && (
            <Button variant="outline" size="sm" onClick={handleCancel} disabled={actionLoading}>
              <X className="mr-2 h-4 w-4" />
              Cancel remaining
            </Button>
          )}
          <Button
            variant="destructive"
            size="sm"
            onClick={() => setDeleteOpen(true)}
            disabled={actionLoading}
          >
            <Trash2 className="mr-2 h-4 w-4" />
            Delete batch
          </Button>
        </div>
      </div>

      <Dialog open={deleteOpen} onOpenChange={setDeleteOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Delete this batch?</DialogTitle>
            <DialogDescription asChild>
              <div className="space-y-2">
                <p>
                  This removes the batch grouping and all {data.total} queue items.
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
            <Button variant="outline" onClick={() => setDeleteOpen(false)} disabled={actionLoading}>
              Cancel
            </Button>
            <Button variant="destructive" onClick={handleDelete} disabled={actionLoading}>
              {actionLoading ? "Deleting…" : "Delete batch"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Overall progress */}
      <Card className="p-4">
        <div className="mb-2 flex items-center justify-between text-sm">
          <span className="font-medium">Progress</span>
          <span className="tabular-nums text-muted-foreground">
            {finished} / {data.total} ({pct}%)
          </span>
        </div>
        <div className="h-3 w-full overflow-hidden rounded-full bg-muted">
          <div className="h-full bg-primary transition-all" style={{ width: `${pct}%` }} />
        </div>
        <div className="mt-2 flex gap-4 text-xs text-muted-foreground">
          <span>Done: {done}</span>
          <span className="text-destructive">Failed: {failed}</span>
          <span>Cancelled: {cancelled}</span>
        </div>
      </Card>

      {/* Items table */}
      <Card>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="w-28">Question ID</TableHead>
              <TableHead>Question</TableHead>
              <TableHead className="w-32">RAG</TableHead>
              <TableHead className="w-32">Web Search</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {grouped.map((row) => (
              <TableRow key={row.question_id}>
                <TableCell className="font-mono text-xs">{row.question_id}</TableCell>
                <TableCell className="max-w-xl truncate" title={row.question_text}>
                  {row.question_text}
                </TableCell>
                <TableCell>
                  <ItemCell item={row.rag} onRetry={handleRetryItem} />
                </TableCell>
                <TableCell>
                  <ItemCell item={row.websearch} onRetry={handleRetryItem} />
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </Card>
    </div>
  );
}
