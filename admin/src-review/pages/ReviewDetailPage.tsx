import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { formatDistanceToNow } from "date-fns";
import { CheckCircle2, Download, ExternalLink, Flag, SkipForward, X } from "lucide-react";
import { ApiError, reviewFetch, getToken } from "@review/lib/review-api";
import { useReviewAuth } from "@review/hooks/use-review-auth";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Separator } from "@/components/ui/separator";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import MarkdownAnswer from "@/components/MarkdownAnswer";
import ModeBadge from "@/components/ModeBadge";
import { useActiveDuration } from "@/hooks/use-active-duration";

// ── Types ──────────────────────────────────────────────────

interface Reference {
  id?: string;
  title?: string;
  source_url?: string;
  relevance_score?: number;
}

interface Evaluation {
  id: number;
  query_log_id: number;
  reviewer_id: number;
  checklist: ChecklistState;
  note: string | null;
  created_at: string;
  updated_at: string | null;
}

interface ReviewedArticle {
  id: number;
  query_log_id: number;
  reviewer_id: number;
  version: number;
  title: string;
  edited_response: string;
  status: string;
  created_at: string;
  updated_at: string | null;
}

interface QueryDetail {
  id: number;
  query_text: string;
  response_text: string | null;
  model_used: string | null;
  references: Reference[];
  scope_declined: boolean;
  cached: boolean;
  latency_ms: number | null;
  ip_address: string | null;
  created_at: string;
  review_status: string;
  mode: "rag" | "websearch";
  evaluation: Evaluation | null;
  latest_article: ReviewedArticle | null;
}

interface ChecklistState {
  answers_question: boolean;
  factually_accurate: boolean;
  sources_relevant: boolean;
  no_hallucinations: boolean;
  appropriate_scope: boolean;
  language_quality: boolean;
  publishable_minor_edits: boolean;
}

const CHECKLIST_LABELS: Record<keyof ChecklistState, string> = {
  answers_question: "Answers the question asked?",
  factually_accurate: "Factually accurate?",
  sources_relevant: "Sources are relevant?",
  no_hallucinations: "No hallucinations?",
  appropriate_scope: "Appropriate scope (EU/Iceland)?",
  language_quality: "Language quality acceptable?",
  publishable_minor_edits: "Publishable with minor edits?",
};

const DEFAULT_CHECKLIST: ChecklistState = {
  answers_question: false,
  factually_accurate: false,
  sources_relevant: false,
  no_hallucinations: false,
  appropriate_scope: false,
  language_quality: false,
  publishable_minor_edits: false,
};

// ── Component ──────────────────────────────────────────────

interface FlagDto {
  id: number;
  article_id: string | null;
  url: string | null;
  flag_type: "outdated" | "irrelevant" | "untrustworthy";
  reviewer_id: number;
  reviewer_username: string;
  reason: string | null;
  created_at: string;
}

interface WebRef {
  number: number;
  text: string;
  url: string | null;
}

function splitAnswerOnReferences(answer: string): { body: string; refs: WebRef[] } {
  // Match Icelandic "Heimildir" or English "References" heading
  const headingMatch = answer.match(/^##\s+(?:Heimildir|References)\s*$/m);
  if (!headingMatch || headingMatch.index === undefined) {
    return { body: answer, refs: [] };
  }
  const body = answer.slice(0, headingMatch.index).replace(/\s+$/, "");
  const section = answer.slice(headingMatch.index + headingMatch[0].length);
  const refs: WebRef[] = [];
  for (const line of section.split("\n")) {
    const itemM = line.match(/^\s*-\s*\[(\d+)\]\s*(.+?)\s*$/);
    if (itemM) {
      const text = itemM[2];
      const urlM = text.match(/https?:\/\/[^\s)\]]+/);
      refs.push({
        number: parseInt(itemM[1], 10),
        text,
        url: urlM ? urlM[0].replace(/[.,;)'"]+$/, "") : null,
      });
    }
  }
  return { body, refs };
}

export default function ReviewDetailPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { username: myUsername } = useReviewAuth();
  const [allDone, setAllDone] = useState(false);

  const { data, isLoading } = useQuery<QueryDetail>({
    queryKey: ["review-query", id],
    queryFn: () => reviewFetch(`/api/v1/review/queries/${id}`),
    enabled: !!id,
  });

  const { data: flags = [] } = useQuery<FlagDto[]>({
    queryKey: ["review-query-flags", id],
    queryFn: () => reviewFetch(`/api/v1/review/queries/${id}/flags`),
    enabled: !!id,
    staleTime: 5_000,
  });

  useEffect(() => {
    // Reset the "all done" banner when the user opens a fresh query
    setAllDone(false);
  }, [id]);

  const invalidate = () =>
    queryClient.invalidateQueries({ queryKey: ["review-query", id] });

  const invalidateFlags = () =>
    queryClient.invalidateQueries({ queryKey: ["review-query-flags", id] });

  const goToNext = async () => {
    if (!id) return;
    try {
      const next = await reviewFetch<{ id: number }>(
        `/api/v1/review/queries/next?exclude_id=${id}`,
      );
      navigate(`/queries/${next.id}`);
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) {
        setAllDone(true);
      } else {
        console.error("Failed to fetch next query", err);
      }
    }
  };

  const onEvaluationSaved = async () => {
    invalidate();
    await goToNext();
  };

  if (isLoading) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-8 w-64" />
        <Skeleton className="h-96 w-full" />
      </div>
    );
  }

  if (!data) {
    return <p className="text-muted-foreground">Query not found.</p>;
  }

  return (
    <div className="space-y-8">
      {/* ── Header ────────────────────────────────────── */}
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <h1 className="text-3xl font-bold">Query #{data.id}</h1>
        <ModeBadge mode={data.mode} size="md" />
        <Badge
          variant={
            data.review_status === "approved"
              ? "default"
              : data.review_status === "reviewed"
                ? "secondary"
                : "outline"
          }
        >
          {data.review_status}
        </Badge>
        <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
          {data.model_used && (
            <span className="rounded-sm bg-secondary font-medium px-1.5 py-0.5">
              {data.model_used}
            </span>
          )}
          {data.latency_ms != null && (
            <span className="rounded-sm bg-secondary font-medium px-1.5 py-0.5">
              {data.latency_ms}ms
            </span>
          )}
          {data.cached && (
            <span className="rounded-sm bg-secondary font-medium px-1.5 py-0.5">cached</span>
          )}
          {data.scope_declined && (
            <span className="rounded-sm bg-secondary font-medium px-1.5 py-0.5">
              scope declined
            </span>
          )}
          <span>
            {formatDistanceToNow(new Date(data.created_at), {
              addSuffix: true,
            })}
          </span>
        </div>
      </div>

      {/* ── Two-column grid ───────────────────────────── */}
      <div className="grid gap-8 lg:grid-cols-[1fr_380px]">
        {/* Left column: query, response, references */}
        <div className="space-y-8">
          {/* Query */}
          <section>
            <h2 className="mb-2 text-xs font-bold uppercase tracking-wider text-muted-foreground">
              Query
            </h2>
            <p className="rounded-sm border-l-4 border-primary bg-secondary/40 p-3 font-mono text-sm">
              {data.query_text}
            </p>
          </section>

          {/* Response */}
          <section>
            <h2 className="mb-2 text-xs font-bold uppercase tracking-wider text-muted-foreground">
              Response
            </h2>
            <div className="border-l-4 border-primary pl-4">
              <MarkdownAnswer>
                {data.mode === "websearch"
                  ? splitAnswerOnReferences(data.response_text ?? "").body || "*No response*"
                  : data.response_text ?? "*No response*"}
              </MarkdownAnswer>
            </div>
          </section>

          {/* Web references (web search mode only) */}
          {data.mode === "websearch" && (() => {
            const { refs: webRefs } = splitAnswerOnReferences(data.response_text ?? "");
            if (webRefs.length === 0) return null;
            return (
              <section>
                <h2 className="mb-2 text-xs font-bold uppercase tracking-wider text-muted-foreground">
                  Web references ({webRefs.length})
                </h2>
                <ul className="space-y-2">
                  {webRefs.map((wref) => {
                    if (!wref.url) return null;
                    const articleFlags = flags.filter((f) => f.url === wref.url);
                    const myFlag = myUsername
                      ? articleFlags.find((f) => f.reviewer_username === myUsername) ?? null
                      : null;
                    const othersCount = articleFlags.length - (myFlag ? 1 : 0);
                    return (
                      <WebReferenceCard
                        key={wref.url}
                        wref={wref}
                        queryLogId={data.id}
                        myFlag={myFlag}
                        othersFlagCount={othersCount}
                        onFlagChanged={invalidateFlags}
                      />
                    );
                  })}
                </ul>
              </section>
            );
          })()}

          {/* References */}
          {data.references.length > 0 && (
            <section>
              <h2 className="mb-2 text-xs font-bold uppercase tracking-wider text-muted-foreground">
                References ({data.references.length})
              </h2>
              <ul className="space-y-2">
                {data.references.map((ref, i) => {
                  const articleFlags = ref.id
                    ? flags.filter((f) => f.article_id === ref.id)
                    : [];
                  const myFlag = myUsername
                    ? articleFlags.find((f) => f.reviewer_username === myUsername) ?? null
                    : null;
                  const othersCount = articleFlags.length - (myFlag ? 1 : 0);
                  return (
                    <ReferenceCard
                      key={ref.id ?? i}
                      ref_={ref}
                      queryLogId={data.id}
                      myFlag={myFlag}
                      othersFlagCount={othersCount}
                      onFlagChanged={invalidateFlags}
                    />
                  );
                })}
              </ul>
            </section>
          )}
        </div>

        {/* Right column: evaluation (sticky) */}
        <div className="lg:sticky lg:top-6 lg:self-start lg:max-h-[calc(100vh-3rem)] lg:overflow-y-auto space-y-3">
          {allDone && (
            <div className="flex items-start gap-2 rounded-md border border-emerald-500/30 bg-emerald-500/10 p-3 text-sm">
              <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-emerald-600" />
              <div>
                <p className="font-medium text-emerald-700 dark:text-emerald-300">
                  You're caught up.
                </p>
                <p className="text-xs text-emerald-700/80 dark:text-emerald-300/80">
                  No more unreviewed queries in the queue.
                </p>
              </div>
            </div>
          )}
          <EvaluationPanel
            key={data.id}
            queryId={data.id}
            existing={data.evaluation}
            onSaved={onEvaluationSaved}
            onSkip={goToNext}
          />
        </div>
      </div>

      {/* ── Article Editor (full-width) ───────────────── */}
      <Separator />
      <ArticleEditor
        queryId={data.id}
        defaultTitle={data.query_text}
        defaultBody={data.response_text ?? ""}
        existing={data.latest_article}
        onSaved={invalidate}
      />
    </div>
  );
}

// ── Evaluation Panel ──────────────────────────────────────

function EvaluationPanel({
  queryId,
  existing,
  onSaved,
  onSkip,
}: {
  queryId: number;
  existing: Evaluation | null;
  onSaved: () => void;
  onSkip: () => void;
}) {
  const [checklist, setChecklist] = useState<ChecklistState>(
    existing?.checklist
      ? { ...DEFAULT_CHECKLIST, ...existing.checklist }
      : DEFAULT_CHECKLIST,
  );
  const [note, setNote] = useState(existing?.note ?? "");
  const duration = useActiveDuration();

  useEffect(() => {
    if (existing) {
      setChecklist({ ...DEFAULT_CHECKLIST, ...existing.checklist });
      setNote(existing.note ?? "");
    }
  }, [existing]);

  const mutation = useMutation({
    mutationFn: () =>
      reviewFetch(`/api/v1/review/queries/${queryId}/evaluate`, {
        method: "POST",
        body: JSON.stringify({
          checklist,
          note: note || null,
          duration_seconds: duration.get(),
        }),
      }),
    onSuccess: onSaved,
  });

  function toggle(key: keyof ChecklistState) {
    setChecklist((prev) => ({ ...prev, [key]: !prev[key] }));
  }

  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="text-sm">Evaluation Checklist</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="space-y-2">
          {(Object.keys(CHECKLIST_LABELS) as (keyof ChecklistState)[]).map(
            (key) => (
              <label
                key={key}
                className="flex cursor-pointer items-center gap-3 rounded-sm border px-3 py-2 transition-colors hover:bg-muted/50"
              >
                <button
                  type="button"
                  role="switch"
                  aria-checked={checklist[key]}
                  onClick={() => toggle(key)}
                  className={`relative inline-flex h-5 w-9 shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors ${
                    checklist[key] ? "bg-primary" : "bg-input"
                  }`}
                >
                  <span
                    className={`pointer-events-none block h-4 w-4 rounded-full bg-background shadow-lg ring-0 transition-transform ${
                      checklist[key] ? "translate-x-4" : "translate-x-0"
                    }`}
                  />
                </button>
                <span className="text-sm">{CHECKLIST_LABELS[key]}</span>
              </label>
            ),
          )}
        </div>

        <div className="space-y-2">
          <label className="text-sm font-bold">Notes</label>
          <Textarea
            placeholder="Optional notes about this evaluation..."
            value={note}
            onChange={(e) => setNote(e.target.value)}
            rows={3}
          />
        </div>

        <div className="space-y-2">
          <Button
            className="w-full"
            onClick={() => mutation.mutate()}
            disabled={mutation.isPending}
          >
            {mutation.isPending ? "Saving..." : "Save & continue"}
          </Button>
          <Button
            variant="ghost"
            className="w-full text-muted-foreground hover:text-foreground"
            onClick={onSkip}
            disabled={mutation.isPending}
          >
            <SkipForward className="mr-2 h-4 w-4" />
            Skip without saving
          </Button>
        </div>
        <p className="text-center text-xs text-muted-foreground">
          Save records your checklist; Skip jumps to another unreviewed query.
        </p>

        {mutation.isError && (
          <p className="text-sm text-destructive">
            Error saving evaluation.
          </p>
        )}
      </CardContent>
    </Card>
  );
}

// ── Article Editor ────────────────────────────────────────

function ArticleEditor({
  queryId,
  defaultTitle,
  defaultBody,
  existing,
  onSaved,
}: {
  queryId: number;
  defaultTitle: string;
  defaultBody: string;
  existing: ReviewedArticle | null;
  onSaved: () => void;
}) {
  const [title, setTitle] = useState(existing?.title ?? defaultTitle);
  const [body, setBody] = useState(existing?.edited_response ?? defaultBody);

  useEffect(() => {
    if (existing) {
      setTitle(existing.title);
      setBody(existing.edited_response);
    }
  }, [existing]);

  const mutation = useMutation({
    mutationFn: () =>
      reviewFetch(`/api/v1/review/queries/${queryId}/article`, {
        method: "POST",
        body: JSON.stringify({ title, edited_response: body }),
      }),
    onSuccess: onSaved,
  });

  function handleExport(fmt: "md" | "docx") {
    const token = getToken();
    const url = `/api/v1/review/queries/${queryId}/export/${fmt}`;
    fetch(url, {
      headers: { Authorization: `Bearer ${token ?? ""}` },
    })
      .then((res) => res.blob())
      .then((blob) => {
        const a = document.createElement("a");
        a.href = URL.createObjectURL(blob);
        a.download = `article.${fmt}`;
        a.click();
        URL.revokeObjectURL(a.href);
      });
  }

  return (
    <section>
      <div className="mb-4 flex items-center justify-between">
        <h2 className="text-xs font-bold uppercase tracking-wider text-muted-foreground">
          Article Editor
        </h2>
        {existing && (
          <span className="text-xs text-muted-foreground">
            v{existing.version}
            {" — saved "}
            {formatDistanceToNow(
              new Date(existing.updated_at ?? existing.created_at),
              { addSuffix: true },
            )}
          </span>
        )}
      </div>

      <div className="space-y-4">
        <div className="space-y-2">
          <label className="text-sm font-bold">Title</label>
          <Input
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="Article title"
          />
        </div>

        <div className="space-y-2">
          <label className="text-sm font-bold">Content</label>
          <Textarea
            value={body}
            onChange={(e) => setBody(e.target.value)}
            rows={16}
            className="font-mono text-sm"
          />
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <Button
            onClick={() => mutation.mutate()}
            disabled={mutation.isPending}
          >
            {mutation.isPending ? "Saving..." : "Save Draft"}
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={() => handleExport("md")}
            disabled={!existing}
          >
            <Download className="mr-2 h-4 w-4" />
            Download .md
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={() => handleExport("docx")}
            disabled={!existing}
          >
            <Download className="mr-2 h-4 w-4" />
            Download .docx
          </Button>
        </div>

        {mutation.isSuccess && (
          <p className="text-sm text-green-600">Draft saved.</p>
        )}
        {mutation.isError && (
          <p className="text-sm text-destructive">Error saving draft.</p>
        )}
      </div>
    </section>
  );
}

// ── ReferenceCard (with outdated-flag affordance) ─────────

function ReferenceCard({
  ref_,
  queryLogId,
  myFlag,
  othersFlagCount,
  onFlagChanged,
}: {
  ref_: Reference;
  queryLogId: number;
  myFlag: FlagDto | null;
  othersFlagCount: number;
  onFlagChanged: () => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);

  const doFlag = async () => {
    if (!ref_.id) return;
    setBusy(true);
    try {
      await reviewFetch("/api/v1/review/flags", {
        method: "POST",
        body: JSON.stringify({
          article_id: ref_.id,
          query_log_id: queryLogId,
          reason: reason.trim() || null,
        }),
      });
      setReason("");
      setExpanded(false);
      onFlagChanged();
    } finally {
      setBusy(false);
    }
  };

  const doUnflag = async () => {
    if (!myFlag) return;
    setBusy(true);
    try {
      await reviewFetch(`/api/v1/review/flags/${myFlag.id}`, { method: "DELETE" });
      onFlagChanged();
    } finally {
      setBusy(false);
    }
  };

  const borderClass = myFlag
    ? "border-l-amber-500"
    : othersFlagCount > 0
      ? "border-l-amber-400/60"
      : "border-l-primary/40";

  return (
    <li
      className={`rounded-sm border border-l-4 ${borderClass} overflow-hidden text-sm`}
    >
      <div className="flex items-start justify-between gap-2 p-2">
        <div className="min-w-0 flex-1">
          <p className="font-medium">{ref_.title ?? "Untitled"}</p>
          {ref_.source_url && (
            <a
              href={ref_.source_url}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1 text-xs text-blue-600 hover:underline"
            >
              {ref_.source_url}
              <ExternalLink className="h-3 w-3" />
            </a>
          )}
        </div>

        <div className="flex shrink-0 items-center gap-2">
          {othersFlagCount > 0 && !myFlag && (
            <span
              className="inline-flex items-center gap-1 rounded-full bg-amber-500/10 px-1.5 py-0.5 text-[11px] font-medium text-amber-700 dark:text-amber-300"
              title={`${othersFlagCount} other reviewer${othersFlagCount === 1 ? "" : "s"} flagged this as outdated`}
            >
              <Flag className="h-3 w-3" />
              {othersFlagCount}
            </span>
          )}
          {ref_.relevance_score != null && (
            <Badge variant="outline" className="shrink-0 text-xs">
              {(ref_.relevance_score * 100).toFixed(0)}%
            </Badge>
          )}
          {!myFlag && !expanded && ref_.id && (
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setExpanded(true)}
              className="h-7 w-7 p-0 text-muted-foreground hover:bg-amber-500/10 hover:text-amber-600 dark:hover:text-amber-400"
              title="Flag as outdated"
            >
              <Flag className="h-3.5 w-3.5" />
            </Button>
          )}
        </div>
      </div>

      {/* Existing flag by me — show the reason inline + unflag */}
      {myFlag && (
        <div className="flex items-start gap-2 border-t border-amber-500/20 bg-amber-500/5 px-3 py-2">
          <Flag className="mt-0.5 h-3.5 w-3.5 shrink-0 text-amber-600 dark:text-amber-400" />
          <div className="flex-1 text-xs">
            <p className="font-medium text-amber-700 dark:text-amber-300">
              You flagged this as outdated
              {othersFlagCount > 0 && (
                <span className="ml-1 font-normal text-amber-700/70 dark:text-amber-300/70">
                  · {othersFlagCount} other reviewer{othersFlagCount === 1 ? "" : "s"} agree
                </span>
              )}
            </p>
            {myFlag.reason && (
              <p className="mt-0.5 text-amber-800/80 dark:text-amber-200/80">
                "{myFlag.reason}"
              </p>
            )}
          </div>
          <Button
            variant="ghost"
            size="sm"
            onClick={doUnflag}
            disabled={busy}
            className="h-6 shrink-0 px-2 text-[11px] text-amber-700 hover:bg-amber-500/15 dark:text-amber-300"
          >
            Unflag
          </Button>
        </div>
      )}

      {/* Expanded flag-entry form */}
      {expanded && !myFlag && (
        <div className="space-y-2 border-t border-amber-500/20 bg-amber-500/5 px-3 py-3">
          <div className="flex items-start gap-2">
            <Flag className="mt-1 h-3.5 w-3.5 shrink-0 text-amber-600 dark:text-amber-400" />
            <div className="flex-1 space-y-2">
              <p className="text-xs font-medium text-amber-700 dark:text-amber-300">
                Flag as outdated
              </p>
              <Textarea
                placeholder="Why is this outdated? (optional — e.g., superseded by newer regulation, broken URL, ...)"
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                rows={2}
                className="border-amber-500/30 bg-background text-xs focus-visible:ring-amber-500/30"
                autoFocus
              />
              <div className="flex items-center gap-2">
                <Button
                  size="sm"
                  onClick={doFlag}
                  disabled={busy}
                  className="h-7 bg-amber-600 text-xs text-white hover:bg-amber-700"
                >
                  <Flag className="mr-1.5 h-3 w-3" />
                  Flag
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => {
                    setExpanded(false);
                    setReason("");
                  }}
                  className="h-7 text-xs text-muted-foreground"
                >
                  <X className="mr-1.5 h-3 w-3" />
                  Cancel
                </Button>
              </div>
            </div>
          </div>
        </div>
      )}
    </li>
  );
}

// ── WebReferenceCard (URL-based, flag types: irrelevant/untrustworthy) ─

const WEB_FLAG_TYPES: Array<{ value: "irrelevant" | "untrustworthy"; label: string }> = [
  { value: "irrelevant", label: "Irrelevant" },
  { value: "untrustworthy", label: "Not trustworthy" },
];

function flagTypeLabel(t: string): string {
  if (t === "irrelevant") return "Irrelevant";
  if (t === "untrustworthy") return "Not trustworthy";
  if (t === "outdated") return "Outdated";
  return t;
}

function domainOf(url: string): string {
  try {
    const u = new URL(url);
    return u.hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}

function WebReferenceCard({
  wref,
  queryLogId,
  myFlag,
  othersFlagCount,
  onFlagChanged,
}: {
  wref: WebRef;
  queryLogId: number;
  myFlag: FlagDto | null;
  othersFlagCount: number;
  onFlagChanged: () => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const [reason, setReason] = useState("");
  const [flagType, setFlagType] = useState<"irrelevant" | "untrustworthy">("irrelevant");
  const [busy, setBusy] = useState(false);

  const url = wref.url!;
  // Strip the URL from display text if it's appended — shows cleaner title
  const title = wref.text.replace(/\s*[—–-]\s*https?:\/\/\S+$/, "").trim();

  const doFlag = async () => {
    setBusy(true);
    try {
      await reviewFetch("/api/v1/review/flags", {
        method: "POST",
        body: JSON.stringify({
          url,
          flag_type: flagType,
          query_log_id: queryLogId,
          reason: reason.trim() || null,
        }),
      });
      setReason("");
      setExpanded(false);
      onFlagChanged();
    } finally {
      setBusy(false);
    }
  };

  const doUnflag = async () => {
    if (!myFlag) return;
    setBusy(true);
    try {
      await reviewFetch(`/api/v1/review/flags/${myFlag.id}`, { method: "DELETE" });
      onFlagChanged();
    } finally {
      setBusy(false);
    }
  };

  const borderClass = myFlag
    ? "border-l-amber-500"
    : othersFlagCount > 0
      ? "border-l-amber-400/60"
      : "border-l-sky-500/40";

  return (
    <li
      className={`rounded-sm border border-l-4 ${borderClass} overflow-hidden text-sm`}
    >
      <div className="flex items-start justify-between gap-2 p-2">
        <div className="min-w-0 flex-1">
          <p className="font-medium">
            <span className="mr-2 inline-flex h-5 min-w-5 items-center justify-center rounded-full bg-sky-500/10 px-1.5 text-[10px] font-semibold text-sky-700 tabular-nums dark:text-sky-300">
              {wref.number}
            </span>
            {title || url}
          </p>
          <a
            href={url}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1 text-xs text-blue-600 hover:underline"
          >
            <span className="font-mono text-[10px] uppercase tracking-wide text-muted-foreground">
              {domainOf(url)}
            </span>
            <ExternalLink className="h-3 w-3" />
          </a>
        </div>

        <div className="flex shrink-0 items-center gap-2">
          {othersFlagCount > 0 && !myFlag && (
            <span
              className="inline-flex items-center gap-1 rounded-full bg-amber-500/10 px-1.5 py-0.5 text-[11px] font-medium text-amber-700 dark:text-amber-300"
              title={`${othersFlagCount} other reviewer${othersFlagCount === 1 ? "" : "s"} flagged this`}
            >
              <Flag className="h-3 w-3" />
              {othersFlagCount}
            </span>
          )}
          {!myFlag && !expanded && (
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setExpanded(true)}
              className="h-7 w-7 p-0 text-muted-foreground hover:bg-amber-500/10 hover:text-amber-600 dark:hover:text-amber-400"
              title="Flag this source"
            >
              <Flag className="h-3.5 w-3.5" />
            </Button>
          )}
        </div>
      </div>

      {/* Existing flag by me */}
      {myFlag && (
        <div className="flex items-start gap-2 border-t border-amber-500/20 bg-amber-500/5 px-3 py-2">
          <Flag className="mt-0.5 h-3.5 w-3.5 shrink-0 text-amber-600 dark:text-amber-400" />
          <div className="flex-1 text-xs">
            <p className="font-medium text-amber-700 dark:text-amber-300">
              You flagged this as {flagTypeLabel(myFlag.flag_type).toLowerCase()}
              {othersFlagCount > 0 && (
                <span className="ml-1 font-normal text-amber-700/70 dark:text-amber-300/70">
                  · {othersFlagCount} other reviewer{othersFlagCount === 1 ? "" : "s"} agree
                </span>
              )}
            </p>
            {myFlag.reason && (
              <p className="mt-0.5 text-amber-800/80 dark:text-amber-200/80">
                "{myFlag.reason}"
              </p>
            )}
          </div>
          <Button
            variant="ghost"
            size="sm"
            onClick={doUnflag}
            disabled={busy}
            className="h-6 shrink-0 px-2 text-[11px] text-amber-700 hover:bg-amber-500/15 dark:text-amber-300"
          >
            Unflag
          </Button>
        </div>
      )}

      {/* Expanded flag-entry form */}
      {expanded && !myFlag && (
        <div className="space-y-3 border-t border-amber-500/20 bg-amber-500/5 px-3 py-3">
          <div className="flex items-start gap-2">
            <Flag className="mt-1 h-3.5 w-3.5 shrink-0 text-amber-600 dark:text-amber-400" />
            <div className="flex-1 space-y-2">
              <p className="text-xs font-medium text-amber-700 dark:text-amber-300">
                Flag this source
              </p>

              {/* Type picker: segmented */}
              <div className="inline-flex rounded-md border border-amber-500/30 bg-background p-0.5 text-xs">
                {WEB_FLAG_TYPES.map((t) => (
                  <button
                    key={t.value}
                    type="button"
                    onClick={() => setFlagType(t.value)}
                    className={`rounded-sm px-3 py-1 transition-colors ${
                      flagType === t.value
                        ? "bg-amber-600 font-medium text-white"
                        : "text-muted-foreground hover:text-foreground"
                    }`}
                  >
                    {t.label}
                  </button>
                ))}
              </div>

              <Textarea
                placeholder={
                  flagType === "irrelevant"
                    ? "Why is this irrelevant to the question? (optional)"
                    : "Why is this source not trustworthy? (optional)"
                }
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                rows={2}
                className="border-amber-500/30 bg-background text-xs focus-visible:ring-amber-500/30"
                autoFocus
              />
              <div className="flex items-center gap-2">
                <Button
                  size="sm"
                  onClick={doFlag}
                  disabled={busy}
                  className="h-7 bg-amber-600 text-xs text-white hover:bg-amber-700"
                >
                  <Flag className="mr-1.5 h-3 w-3" />
                  Flag as {flagTypeLabel(flagType).toLowerCase()}
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => {
                    setExpanded(false);
                    setReason("");
                  }}
                  className="h-7 text-xs text-muted-foreground"
                >
                  <X className="mr-1.5 h-3 w-3" />
                  Cancel
                </Button>
              </div>
            </div>
          </div>
        </div>
      )}
    </li>
  );
}
