import { Fragment, useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { formatDistanceToNow } from "date-fns";
import {
  ArrowDown,
  ArrowUp,
  ChevronDown,
  ChevronRight,
  Download,
  Eye,
  EyeOff,
  FileCode2,
  Search,
} from "lucide-react";
import { apiDownload, apiFetch, ApiError } from "@/lib/api";
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

interface RetrievalCandidate {
  rank: number;
  id: string;
  title: string;
  source_url: string;
  rrf_score: number;
  vector_score: number | null;
  vector_rank: number | null;
  lexical_rank: number | null;
  in_prompt: boolean;
}

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
  mode: string;
  retrieval_candidates: RetrievalCandidate[];
  retrieval_top_k: number | null;
}

type AnnotationLabel = "should_cite" | "correct" | "irrelevant";

interface RetrievalAnnotationList {
  query_log_id: number;
  annotations: Array<{ article_id: string; label: AnnotationLabel }>;
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

const PER_PAGE = 30;

export default function QueryLogPage() {
  const queryClient = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();
  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  const [exporting, setExporting] = useState(false);

  // ── URL-backed state (shareable / bookmarkable) ──────────────
  const page = Math.max(1, Number(searchParams.get("page") || "1"));
  const search = searchParams.get("search") || "";
  const cachedFilter = searchParams.get("cached") || "all";
  const modelFilter = searchParams.get("model") || "all";
  const order = searchParams.get("order") === "asc" ? "asc" : "desc";
  const fromDate = searchParams.get("from") || "";
  const toDate = searchParams.get("to") || "";

  // Debounce the search box into the URL so typing doesn't spam history/fetches.
  const [searchInput, setSearchInput] = useState(search);
  useEffect(() => {
    const t = setTimeout(() => {
      if (searchInput !== (searchParams.get("search") || "")) {
        updateParams({ search: searchInput });
      }
    }, 300);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchInput]);

  function updateParams(
    updates: Record<string, string>,
    opts: { resetPage?: boolean } = {},
  ) {
    const { resetPage = true } = opts;
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        for (const [k, v] of Object.entries(updates)) {
          if (v === "" || v == null) next.delete(k);
          else next.set(k, v);
        }
        if (resetPage && !("page" in updates)) next.set("page", "1");
        return next;
      },
      { replace: false },
    );
  }

  // ── Build filter params shared by the list query and the export ──
  function filterParams(): URLSearchParams {
    const p = new URLSearchParams();
    p.set("order", order);
    if (search) p.set("search", search);
    if (cachedFilter === "yes") p.set("cached", "true");
    if (cachedFilter === "no") p.set("cached", "false");
    if (modelFilter !== "all") p.set("model_used", modelFilter);
    if (fromDate) p.set("date_from", `${fromDate}T00:00:00`);
    if (toDate) p.set("date_to", `${toDate}T23:59:59`);
    return p;
  }

  const listParams = filterParams();
  listParams.set("page", String(page));
  listParams.set("per_page", String(PER_PAGE));

  const logs = useQuery<QueryLogList>({
    queryKey: [
      "query-logs",
      page,
      search,
      cachedFilter,
      modelFilter,
      order,
      fromDate,
      toDate,
    ],
    queryFn: () => apiFetch(`/api/v1/admin/query-log?${listParams}`),
  });

  const stats = useQuery<QueryLogStats>({
    queryKey: ["query-log-stats"],
    queryFn: () => apiFetch("/api/v1/admin/query-log/stats"),
    refetchInterval: 30_000,
  });

  async function toggleExclusion(queryId: number, newStatus: string) {
    await apiFetch(`/api/v1/admin/query-log/${queryId}/review-status`, {
      method: "PATCH",
      body: JSON.stringify({ review_status: newStatus }),
    });
    queryClient.invalidateQueries({ queryKey: ["query-logs"] });
  }

  async function handleExport() {
    setExporting(true);
    try {
      await apiDownload(
        `/api/v1/admin/query-log/export?${filterParams()}`,
        "query_log.csv",
      );
    } catch (e) {
      const msg = e instanceof ApiError ? e.message : String(e);
      alert(`Export failed: ${msg}`);
    } finally {
      setExporting(false);
    }
  }

  function toggleExpand(id: number) {
    setExpanded((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  }

  const st = stats.data;
  const totalPages = logs.data?.total_pages ?? 1;

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-3xl font-bold">Query Log</h1>
        <Button
          variant="outline"
          size="sm"
          onClick={handleExport}
          disabled={exporting}
        >
          <Download className="mr-1.5 h-4 w-4" />
          {exporting ? "Exporting…" : "Export CSV"}
        </Button>
      </div>

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
      <div className="flex flex-wrap items-end gap-3">
        <div className="relative max-w-xs flex-1">
          <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            placeholder="Search queries..."
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            className="pl-10"
          />
        </div>
        <Select
          value={cachedFilter}
          onValueChange={(v) => updateParams({ cached: v === "all" ? "" : v })}
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
          onValueChange={(v) => updateParams({ model: v === "all" ? "" : v })}
        >
          <SelectTrigger className="w-40">
            <SelectValue placeholder="Model" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All Models</SelectItem>
            <SelectItem value="pro">Pro</SelectItem>
            <SelectItem value="flash">Flash</SelectItem>
          </SelectContent>
        </Select>
        <div className="flex flex-col gap-1">
          <label className="text-xs text-muted-foreground">From</label>
          <Input
            type="date"
            value={fromDate}
            max={toDate || undefined}
            onChange={(e) => updateParams({ from: e.target.value })}
            className="w-40"
          />
        </div>
        <div className="flex flex-col gap-1">
          <label className="text-xs text-muted-foreground">To</label>
          <Input
            type="date"
            value={toDate}
            min={fromDate || undefined}
            onChange={(e) => updateParams({ to: e.target.value })}
            className="w-40"
          />
        </div>
        {(fromDate || toDate || search || cachedFilter !== "all" || modelFilter !== "all") && (
          <Button
            variant="ghost"
            size="sm"
            onClick={() => {
              setSearchInput("");
              setSearchParams({}, { replace: false });
            }}
          >
            Clear filters
          </Button>
        )}
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
                <TableHead>
                  <button
                    type="button"
                    onClick={() =>
                      updateParams({ order: order === "desc" ? "asc" : "desc" })
                    }
                    className="flex items-center gap-1 font-medium hover:text-foreground"
                  >
                    Time
                    {order === "desc" ? (
                      <ArrowDown className="h-3.5 w-3.5" />
                    ) : (
                      <ArrowUp className="h-3.5 w-3.5" />
                    )}
                  </button>
                </TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {logs.data?.logs.map((log) => (
                <Fragment key={log.id}>
                  <TableRow
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
                      {log.mode === "error" && (
                        <Badge variant="destructive">error</Badge>
                      )}
                      {log.mode === "websearch" && (
                        <Badge variant="secondary">web</Badge>
                      )}
                      {log.review_status === "excluded" && (
                        <Badge variant="outline" className="text-xs text-muted-foreground">excluded</Badge>
                      )}
                      {log.cached && <Badge variant="secondary">cached</Badge>}
                      {log.scope_declined && (
                        <Badge variant="destructive">declined</Badge>
                      )}
                      {log.mode !== "error" && !log.cached && !log.scope_declined && log.review_status !== "excluded" && <Badge>live</Badge>}
                    </TableCell>
                    <TableCell className="text-xs text-muted-foreground">
                      {log.latency_ms != null ? `${log.latency_ms}ms` : "—"}
                    </TableCell>
                    <TableCell className="text-xs text-muted-foreground">
                      {log.ip_address ?? "—"}
                    </TableCell>
                    <TableCell
                      className="text-xs text-muted-foreground"
                      title={new Date(log.created_at).toLocaleString()}
                    >
                      {formatDistanceToNow(new Date(log.created_at), {
                        addSuffix: true,
                      })}
                    </TableCell>
                  </TableRow>
                  {expanded.has(log.id) && (
                    <TableRow>
                      {/* whitespace-normal overrides TableCell's base
                          whitespace-nowrap: without it nothing in this detail
                          block may soft-wrap (nowrap beats overflow-wrap), so
                          the references list stretches the table past the
                          viewport. */}
                      <TableCell colSpan={7} className="whitespace-normal bg-secondary/50 p-4 detail-accent">
                        <div className="space-y-3 text-sm">
                          <div className="flex items-center justify-between">
                            <p className="font-medium">Response:</p>
                            <div className="flex items-center gap-2">
                              {log.review_status !== "pending" && (
                                <Badge variant="outline" className="text-xs">
                                  {log.review_status}
                                </Badge>
                              )}
                              {log.response_text && (
                                <Button
                                  variant="outline"
                                  size="sm"
                                  onClick={async (e) => {
                                    e.stopPropagation();
                                    try {
                                      await apiDownload(
                                        `/api/v1/admin/reviews/${log.id}/export/visindavefur`,
                                        `${log.id}_visindavefur.html`,
                                      );
                                    } catch (err) {
                                      const msg =
                                        err instanceof ApiError
                                          ? err.message
                                          : String(err);
                                      alert(`Export failed: ${msg}`);
                                    }
                                  }}
                                  title="Download this answer as a Vísindavefur-format HTML snippet (uses the reviewed_articles edit when one exists, otherwise the raw LLM response)"
                                >
                                  <FileCode2 className="mr-1.5 h-3.5 w-3.5" />
                                  Download VV format
                                </Button>
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
                          {/* [overflow-wrap:anywhere] is load-bearing: long
                              citation URLs are unbreakable words, and a table
                              cell's min-content width grows to fit them —
                              blowing the whole table past the viewport.
                              `anywhere` (unlike break-words) shrinks the
                              intrinsic width so the cell wraps instead. */}
                          <p className="max-h-40 overflow-y-auto whitespace-pre-wrap break-words [overflow-wrap:anywhere] text-muted-foreground">
                            {log.response_text ?? "No response"}
                          </p>
                          {log.references.length > 0 && (
                            <>
                              <p className="font-medium">
                                References ({log.references.length}):
                              </p>
                              <ul className="list-inside list-disc break-words [overflow-wrap:anywhere] text-muted-foreground">
                                {log.references.map((ref) => (
                                  <li key={ref.id}>
                                    {ref.title} ({ref.id})
                                  </li>
                                ))}
                              </ul>
                            </>
                          )}
                          {(log.retrieval_candidates?.length ?? 0) > 0 && (
                            <CandidatesPanel log={log} />
                          )}
                        </div>
                      </TableCell>
                    </TableRow>
                  )}
                </Fragment>
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

          {logs.data && (
            <div className="flex flex-wrap items-center justify-between gap-3">
              <span className="text-sm text-muted-foreground">
                {logs.data.total.toLocaleString()} queries
              </span>
              <div className="flex items-center gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  disabled={page <= 1}
                  onClick={() => updateParams({ page: String(page - 1) }, { resetPage: false })}
                >
                  Previous
                </Button>
                <span className="text-sm text-muted-foreground">Page</span>
                <Input
                  type="number"
                  min={1}
                  max={totalPages}
                  value={page}
                  onChange={(e) => {
                    const p = Math.min(
                      Math.max(1, Number(e.target.value) || 1),
                      totalPages,
                    );
                    updateParams({ page: String(p) }, { resetPage: false });
                  }}
                  className="w-16 text-center"
                />
                <span className="text-sm text-muted-foreground">of {totalPages}</span>
                <Button
                  variant="outline"
                  size="sm"
                  disabled={page >= totalPages}
                  onClick={() => updateParams({ page: String(page + 1) }, { resetPage: false })}
                >
                  Next
                </Button>
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}

/**
 * Retrieval-candidate list for one query, split at the top_k cutoff.
 *
 * In-prompt candidates can be labelled "correct" / "irrelevant"; below-cutoff
 * candidates can be labelled "should have cited". The labels are stored in
 * retrieval_annotations and double as ground truth for a future retrieval
 * evaluation set.
 */
function CandidatesPanel({ log }: { log: QueryLogEntry }) {
  const queryClient = useQueryClient();
  const annotations = useQuery<RetrievalAnnotationList>({
    queryKey: ["retrieval-annotations", log.id],
    queryFn: () => apiFetch(`/api/v1/admin/query-log/${log.id}/annotations`),
  });
  const [saving, setSaving] = useState<string | null>(null);

  const labelByArticle = new Map(
    (annotations.data?.annotations ?? []).map((a) => [a.article_id, a.label]),
  );
  const citedIds = new Set(log.references.map((r) => r.id));

  async function toggleLabel(articleId: string, label: AnnotationLabel) {
    setSaving(articleId);
    try {
      if (labelByArticle.get(articleId) === label) {
        await apiFetch(
          `/api/v1/admin/query-log/${log.id}/annotations/${encodeURIComponent(articleId)}`,
          { method: "DELETE" },
        );
      } else {
        await apiFetch(`/api/v1/admin/query-log/${log.id}/annotations`, {
          method: "PUT",
          body: JSON.stringify({ article_id: articleId, label }),
        });
      }
      queryClient.invalidateQueries({
        queryKey: ["retrieval-annotations", log.id],
      });
    } catch (e) {
      const msg = e instanceof ApiError ? e.message : String(e);
      alert(`Could not save annotation: ${msg}`);
    } finally {
      setSaving(null);
    }
  }

  function candidateRow(c: RetrievalCandidate) {
    const active = labelByArticle.get(c.id);
    return (
      <div
        key={c.id}
        className="flex flex-wrap items-center gap-2 rounded border bg-background/60 px-2 py-1.5"
      >
        <span className="w-8 shrink-0 text-right font-mono text-xs text-muted-foreground">
          #{c.rank}
        </span>
        <a
          href={c.source_url}
          target="_blank"
          rel="noreferrer"
          className="min-w-0 flex-1 truncate text-xs hover:underline"
          title={`${c.title} (${c.id})`}
          onClick={(e) => e.stopPropagation()}
        >
          {c.title}
        </a>
        <span className="shrink-0 font-mono text-[10px] text-muted-foreground">
          {c.vector_score != null ? `vec ${c.vector_score.toFixed(3)}` : "vec —"}
          {c.lexical_rank != null ? ` · lex #${c.lexical_rank}` : ""}
        </span>
        {citedIds.has(c.id) && (
          <Badge variant="secondary" className="text-[10px]">
            cited
          </Badge>
        )}
        <span className="flex gap-1" onClick={(e) => e.stopPropagation()}>
          {c.in_prompt ? (
            <>
              <Button
                variant={active === "correct" ? "default" : "outline"}
                size="sm"
                className="h-6 px-2 text-[11px]"
                disabled={saving === c.id}
                onClick={() => toggleLabel(c.id, "correct")}
                title="Mark this reference as correctly cited"
              >
                Correct
              </Button>
              <Button
                variant={active === "irrelevant" ? "destructive" : "outline"}
                size="sm"
                className="h-6 px-2 text-[11px]"
                disabled={saving === c.id}
                onClick={() => toggleLabel(c.id, "irrelevant")}
                title="Mark this reference as not relevant to the question"
              >
                Not relevant
              </Button>
            </>
          ) : (
            <Button
              variant={active === "should_cite" ? "default" : "outline"}
              size="sm"
              className="h-6 px-2 text-[11px]"
              disabled={saving === c.id}
              onClick={() => toggleLabel(c.id, "should_cite")}
              title="Mark this article as one the answer should have cited"
            >
              Should have cited
            </Button>
          )}
        </span>
      </div>
    );
  }

  const inPrompt = log.retrieval_candidates.filter((c) => c.in_prompt);
  const belowCutoff = log.retrieval_candidates.filter((c) => !c.in_prompt);

  return (
    <div className="space-y-2">
      <p className="font-medium">
        Retrieval candidates ({log.retrieval_candidates.length}
        {log.retrieval_top_k != null ? `, k=${log.retrieval_top_k}` : ""}):
      </p>
      {inPrompt.length > 0 && (
        <div className="space-y-1">
          <p className="text-xs uppercase tracking-wide text-muted-foreground">
            Sent to model
          </p>
          {inPrompt.map(candidateRow)}
        </div>
      )}
      {belowCutoff.length > 0 && (
        <div className="space-y-1">
          <p className="text-xs uppercase tracking-wide text-muted-foreground">
            Below cutoff (not sent)
          </p>
          {belowCutoff.map(candidateRow)}
        </div>
      )}
    </div>
  );
}

function MiniStat({ label, value }: { label: string; value: string | number }) {
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
