import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { AlertTriangle, ArrowLeft, BookOpen, Globe, RotateCw, Sparkles, Trash2, X } from "lucide-react";
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
  response_empty: boolean;
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
    if (item.response_empty) {
      return (
        <a
          href={`/review/queries/${item.query_log_id}`}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center gap-1 text-sm"
          title="Marked done but the stored response is blank"
        >
          <Badge className="border-rose-300 bg-rose-50 text-rose-900 hover:bg-rose-100 dark:border-rose-700/50 dark:bg-rose-950/40 dark:text-rose-100">
            <AlertTriangle className="mr-1 h-3 w-3" />
            empty
          </Badge>
        </a>
      );
    }
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
  const [regenMode, setRegenMode] = useState<"rag" | "websearch" | null>(null);
  const [regenResult, setRegenResult] = useState<{
    items_reset: number;
    logs_excluded: number;
    items_processing_skipped: number;
  } | null>(null);
  const [emptyOpen, setEmptyOpen] = useState(false);
  const [emptyResult, setEmptyResult] = useState<{
    items_reset: number;
    logs_excluded: number;
    items_processing_skipped: number;
  } | null>(null);

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

  const handleResalvageEmpty = async () => {
    if (!id) return;
    setActionLoading(true);
    try {
      const res = await apiFetch<{
        items_reset: number;
        logs_excluded: number;
        items_processing_skipped: number;
      }>(`/api/v1/admin/batches/${id}/resalvage-empty`, { method: "POST" });
      setEmptyResult(res);
      await fetchBatch();
    } catch (e) {
      console.error(e);
    } finally {
      setActionLoading(false);
    }
  };

  const handleRegenerate = async () => {
    if (!id || !regenMode) return;
    setActionLoading(true);
    try {
      const res = await apiFetch<{
        items_reset: number;
        logs_excluded: number;
        items_processing_skipped: number;
      }>(`/api/v1/admin/batches/${id}/regenerate?mode=${regenMode}`, {
        method: "POST",
      });
      setRegenResult(res);
      await fetchBatch();
    } catch (e) {
      console.error(e);
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

  // Per-mode counts for regenerate buttons + dialog copy (declared before
  // the early returns to obey the Rules of Hooks).
  const modeStats = useMemo(() => {
    const empty = () => ({
      total: 0, done: 0, failed: 0, pending: 0, processing: 0, cancelled: 0,
      empty_response: 0,
    });
    const stats = { rag: empty(), websearch: empty() };
    for (const it of data?.items ?? []) {
      const s = it.mode === "rag" ? stats.rag : it.mode === "websearch" ? stats.websearch : null;
      if (!s) continue;
      s.total += 1;
      if (it.status === "done") s.done += 1;
      else if (it.status === "failed") s.failed += 1;
      else if (it.status === "pending") s.pending += 1;
      else if (it.status === "processing") s.processing += 1;
      else if (it.status === "cancelled") s.cancelled += 1;
      if (it.response_empty) s.empty_response += 1;
    }
    return stats;
  }, [data?.items]);
  const emptyResponseTotal = modeStats.rag.empty_response + modeStats.websearch.empty_response;

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
        <div className="flex flex-wrap items-center gap-2">
          <div className="flex items-center gap-1.5 rounded-md border border-dashed border-border/60 bg-muted/30 px-1.5 py-1">
            <span className="pl-1 pr-0.5 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
              Regenerate
            </span>
            <Button
              variant="outline"
              size="sm"
              onClick={() => { setRegenResult(null); setRegenMode("rag"); }}
              disabled={actionLoading || modeStats.rag.total === 0}
              className="border-amber-300 bg-amber-50 text-amber-900 hover:bg-amber-100 hover:text-amber-900 dark:border-amber-700/50 dark:bg-amber-950/40 dark:text-amber-100 dark:hover:bg-amber-900/40"
              title="Re-queue every RAG item in this batch and hide the existing answers from the reviewer queue"
            >
              <BookOpen className="mr-1.5 h-4 w-4" />
              RAG
              <span className="ml-1.5 rounded-full bg-amber-200/70 px-1.5 py-0.5 text-[10px] font-semibold tabular-nums text-amber-900 dark:bg-amber-800/60 dark:text-amber-100">
                {modeStats.rag.total}
              </span>
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={() => { setRegenResult(null); setRegenMode("websearch"); }}
              disabled={actionLoading || modeStats.websearch.total === 0}
              className="border-sky-300 bg-sky-50 text-sky-900 hover:bg-sky-100 hover:text-sky-900 dark:border-sky-700/50 dark:bg-sky-950/40 dark:text-sky-100 dark:hover:bg-sky-900/40"
              title="Re-queue every web-search item in this batch and hide the existing answers from the reviewer queue"
            >
              <Globe className="mr-1.5 h-4 w-4" />
              Web search
              <span className="ml-1.5 rounded-full bg-sky-200/70 px-1.5 py-0.5 text-[10px] font-semibold tabular-nums text-sky-900 dark:bg-sky-800/60 dark:text-sky-100">
                {modeStats.websearch.total}
              </span>
            </Button>
            {emptyResponseTotal > 0 && (
              <>
                <span aria-hidden className="h-5 w-px bg-border/60" />
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => { setEmptyResult(null); setEmptyOpen(true); }}
                  disabled={actionLoading}
                  className="border-rose-300 bg-rose-50 text-rose-900 hover:bg-rose-100 hover:text-rose-900 dark:border-rose-700/50 dark:bg-rose-950/40 dark:text-rose-100 dark:hover:bg-rose-900/40"
                  title="Find items marked done but whose stored answer is blank, and re-queue them"
                >
                  <AlertTriangle className="mr-1.5 h-4 w-4" />
                  Empty
                  <span className="ml-1.5 rounded-full bg-rose-200/70 px-1.5 py-0.5 text-[10px] font-semibold tabular-nums text-rose-900 dark:bg-rose-800/60 dark:text-rose-100">
                    {emptyResponseTotal}
                  </span>
                </Button>
              </>
            )}
          </div>
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

      <Dialog
        open={regenMode !== null}
        onOpenChange={(o) => {
          if (!o) { setRegenMode(null); setRegenResult(null); }
        }}
      >
        <DialogContent className="max-w-lg">
          {regenMode && (() => {
            const isRag = regenMode === "rag";
            const stats = isRag ? modeStats.rag : modeStats.websearch;
            const accent = isRag
              ? "text-amber-700 dark:text-amber-300"
              : "text-sky-700 dark:text-sky-300";
            const chip = isRag
              ? "bg-amber-100 text-amber-900 dark:bg-amber-950 dark:text-amber-100 border-amber-200 dark:border-amber-800"
              : "bg-sky-100 text-sky-900 dark:bg-sky-950 dark:text-sky-100 border-sky-200 dark:border-sky-800";
            const modeLabel = isRag ? "RAG" : "web-search";
            const Icon = isRag ? BookOpen : Globe;
            return (
              <>
                <DialogHeader>
                  <div className={`mb-2 inline-flex items-center gap-2 rounded-full border px-2.5 py-0.5 text-xs font-medium ${chip} w-fit`}>
                    <Icon className="h-3.5 w-3.5" />
                    {modeLabel}
                  </div>
                  <DialogTitle className="flex items-center gap-2">
                    <Sparkles className={`h-5 w-5 ${accent}`} />
                    Regenerate every {modeLabel} answer?
                  </DialogTitle>
                  <DialogDescription asChild>
                    <div className="space-y-3 pt-2 text-sm">
                      {!regenResult && (
                        <>
                          <p>
                            All <span className="font-semibold text-foreground">{stats.total}</span>{" "}
                            {modeLabel} items in this batch will be re-queued with the current
                            system prompt. The worker uses the Pro model and bypasses the query cache.
                          </p>
                          <div className="rounded-md border bg-muted/40 p-3 text-xs leading-relaxed">
                            <p className="mb-1 font-medium text-foreground">What changes</p>
                            <ul className="ml-4 list-disc space-y-1 text-muted-foreground">
                              <li>
                                Existing answers drop out of the reviewer queue (marked{" "}
                                <code className="rounded bg-background px-1 py-0.5">excluded</code>)
                                but are not deleted.
                              </li>
                              <li>
                                Reviewer evaluations and article drafts already saved against those
                                answers remain attached to the excluded rows.
                              </li>
                              <li>
                                Items already{" "}
                                <code className="rounded bg-background px-1 py-0.5">processing</code>{" "}
                                by the worker are skipped — re-run this action once they finish to
                                catch the stragglers.
                              </li>
                            </ul>
                          </div>
                          {stats.total > 500 && (
                            <p className={`text-xs ${accent}`}>
                              Heads up: {stats.total} calls is a meaningful slice of the Pro daily
                              quota.
                            </p>
                          )}
                        </>
                      )}
                      {regenResult && (
                        <div className="space-y-2 rounded-md border border-emerald-200 bg-emerald-50 p-3 text-sm dark:border-emerald-900/60 dark:bg-emerald-950/40">
                          <p className="font-medium text-emerald-900 dark:text-emerald-100">
                            Queued for regeneration.
                          </p>
                          <ul className="ml-4 list-disc space-y-0.5 text-xs text-emerald-900/90 dark:text-emerald-100/90">
                            <li>{regenResult.items_reset} items reset to pending</li>
                            <li>{regenResult.logs_excluded} old answers excluded from review</li>
                            {regenResult.items_processing_skipped > 0 && (
                              <li>
                                {regenResult.items_processing_skipped} skipped (worker was mid-flight
                                — rerun when they settle)
                              </li>
                            )}
                          </ul>
                        </div>
                      )}
                    </div>
                  </DialogDescription>
                </DialogHeader>
                <DialogFooter>
                  {!regenResult ? (
                    <>
                      <Button
                        variant="ghost"
                        onClick={() => setRegenMode(null)}
                        disabled={actionLoading}
                      >
                        Cancel
                      </Button>
                      <Button
                        onClick={handleRegenerate}
                        disabled={actionLoading || stats.total === 0}
                        className={
                          isRag
                            ? "bg-amber-600 text-white hover:bg-amber-700 dark:bg-amber-600 dark:hover:bg-amber-500"
                            : "bg-sky-600 text-white hover:bg-sky-700 dark:bg-sky-600 dark:hover:bg-sky-500"
                        }
                      >
                        <Sparkles className="mr-2 h-4 w-4" />
                        {actionLoading
                          ? "Queueing…"
                          : `Regenerate ${stats.total} ${modeLabel} answers`}
                      </Button>
                    </>
                  ) : (
                    <Button
                      onClick={() => { setRegenMode(null); setRegenResult(null); }}
                      variant="outline"
                    >
                      Close
                    </Button>
                  )}
                </DialogFooter>
              </>
            );
          })()}
        </DialogContent>
      </Dialog>

      <Dialog
        open={emptyOpen}
        onOpenChange={(o) => {
          if (!o) { setEmptyOpen(false); setEmptyResult(null); }
        }}
      >
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <div className="mb-2 inline-flex w-fit items-center gap-2 rounded-full border border-rose-200 bg-rose-50 px-2.5 py-0.5 text-xs font-medium text-rose-900 dark:border-rose-800 dark:bg-rose-950 dark:text-rose-100">
              <AlertTriangle className="h-3.5 w-3.5" />
              silent failures
            </div>
            <DialogTitle className="flex items-center gap-2">
              <Sparkles className="h-5 w-5 text-rose-600 dark:text-rose-400" />
              Regenerate blank answers?
            </DialogTitle>
            <DialogDescription asChild>
              <div className="space-y-3 pt-2 text-sm">
                {!emptyResult && (
                  <>
                    <p>
                      We found{" "}
                      <span className="font-semibold text-foreground">
                        {emptyResponseTotal}
                      </span>{" "}
                      item
                      {emptyResponseTotal === 1 ? "" : "s"} marked{" "}
                      <code className="rounded bg-muted px-1 py-0.5">done</code> whose stored
                      response is blank — the model returned an empty string without raising,
                      so the worker accepted it as success.
                    </p>
                    <div className="grid grid-cols-2 gap-2 text-xs">
                      <div className="rounded-md border border-amber-200 bg-amber-50/70 p-2 dark:border-amber-900/60 dark:bg-amber-950/30">
                        <div className="flex items-center gap-1.5 text-amber-900 dark:text-amber-100">
                          <BookOpen className="h-3.5 w-3.5" />
                          <span className="font-medium">RAG</span>
                        </div>
                        <p className="mt-1 tabular-nums text-amber-900/80 dark:text-amber-100/80">
                          {modeStats.rag.empty_response} blank
                        </p>
                      </div>
                      <div className="rounded-md border border-sky-200 bg-sky-50/70 p-2 dark:border-sky-900/60 dark:bg-sky-950/30">
                        <div className="flex items-center gap-1.5 text-sky-900 dark:text-sky-100">
                          <Globe className="h-3.5 w-3.5" />
                          <span className="font-medium">Web search</span>
                        </div>
                        <p className="mt-1 tabular-nums text-sky-900/80 dark:text-sky-100/80">
                          {modeStats.websearch.empty_response} blank
                        </p>
                      </div>
                    </div>
                    <div className="rounded-md border bg-muted/40 p-3 text-xs leading-relaxed">
                      <p className="mb-1 font-medium text-foreground">What happens</p>
                      <ul className="ml-4 list-disc space-y-1 text-muted-foreground">
                        <li>
                          Each blank <code className="rounded bg-background px-1 py-0.5">query_log</code>{" "}
                          row is marked <code className="rounded bg-background px-1 py-0.5">excluded</code>{" "}
                          so it disappears from the reviewer queue.
                        </li>
                        <li>
                          The batch items are reset to{" "}
                          <code className="rounded bg-background px-1 py-0.5">pending</code>{" "}
                          and the worker will re-run them (Pro model, cache bypassed).
                        </li>
                        <li>
                          Items currently{" "}
                          <code className="rounded bg-background px-1 py-0.5">processing</code>{" "}
                          are skipped — re-run this once they finish.
                        </li>
                      </ul>
                    </div>
                  </>
                )}
                {emptyResult && (
                  <div className="space-y-2 rounded-md border border-emerald-200 bg-emerald-50 p-3 text-sm dark:border-emerald-900/60 dark:bg-emerald-950/40">
                    <p className="font-medium text-emerald-900 dark:text-emerald-100">
                      Queued for regeneration.
                    </p>
                    <ul className="ml-4 list-disc space-y-0.5 text-xs text-emerald-900/90 dark:text-emerald-100/90">
                      <li>{emptyResult.items_reset} items reset to pending</li>
                      <li>{emptyResult.logs_excluded} blank answers excluded from review</li>
                      {emptyResult.items_processing_skipped > 0 && (
                        <li>
                          {emptyResult.items_processing_skipped} skipped (worker was mid-flight —
                          rerun when they settle)
                        </li>
                      )}
                    </ul>
                  </div>
                )}
              </div>
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            {!emptyResult ? (
              <>
                <Button
                  variant="ghost"
                  onClick={() => setEmptyOpen(false)}
                  disabled={actionLoading}
                >
                  Cancel
                </Button>
                <Button
                  onClick={handleResalvageEmpty}
                  disabled={actionLoading || emptyResponseTotal === 0}
                  className="bg-rose-600 text-white hover:bg-rose-700 dark:bg-rose-600 dark:hover:bg-rose-500"
                >
                  <Sparkles className="mr-2 h-4 w-4" />
                  {actionLoading
                    ? "Queueing…"
                    : `Regenerate ${emptyResponseTotal} blank answer${emptyResponseTotal === 1 ? "" : "s"}`}
                </Button>
              </>
            ) : (
              <Button
                onClick={() => { setEmptyOpen(false); setEmptyResult(null); }}
                variant="outline"
              >
                Close
              </Button>
            )}
          </DialogFooter>
        </DialogContent>
      </Dialog>

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
