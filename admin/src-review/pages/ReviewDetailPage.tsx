import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { formatDistanceToNow } from "date-fns";
import { Download } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { reviewFetch, getToken } from "@review/lib/review-api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Separator } from "@/components/ui/separator";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";

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
}

const CHECKLIST_LABELS: Record<keyof ChecklistState, string> = {
  answers_question: "Answers the question asked?",
  factually_accurate: "Factually accurate?",
  sources_relevant: "Sources are relevant?",
  no_hallucinations: "No hallucinations?",
  appropriate_scope: "Appropriate scope (EU/Iceland)?",
  language_quality: "Language quality acceptable?",
};

const DEFAULT_CHECKLIST: ChecklistState = {
  answers_question: false,
  factually_accurate: false,
  sources_relevant: false,
  no_hallucinations: false,
  appropriate_scope: false,
  language_quality: false,
};

// ── Component ──────────────────────────────────────────────

export default function ReviewDetailPage() {
  const { id } = useParams<{ id: string }>();
  const queryClient = useQueryClient();

  const { data, isLoading } = useQuery<QueryDetail>({
    queryKey: ["review-query", id],
    queryFn: () => reviewFetch(`/api/v1/review/queries/${id}`),
    enabled: !!id,
  });

  const invalidate = () =>
    queryClient.invalidateQueries({ queryKey: ["review-query", id] });

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
            <div className="border-l-4 border-primary pl-4 prose prose-sm max-w-none">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>
                {data.response_text ?? "*No response*"}
              </ReactMarkdown>
            </div>
          </section>

          {/* References */}
          {data.references.length > 0 && (
            <section>
              <h2 className="mb-2 text-xs font-bold uppercase tracking-wider text-muted-foreground">
                References ({data.references.length})
              </h2>
              <ul className="space-y-2">
                {data.references.map((ref, i) => (
                  <li
                    key={ref.id ?? i}
                    className="flex items-start justify-between gap-2 rounded-sm border border-l-4 border-l-primary/40 p-2 text-sm"
                  >
                    <div>
                      <p className="font-medium">
                        {ref.title ?? "Untitled"}
                      </p>
                      {ref.source_url && (
                        <a
                          href={ref.source_url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="text-xs text-blue-600 hover:underline"
                        >
                          {ref.source_url}
                        </a>
                      )}
                    </div>
                    {ref.relevance_score != null && (
                      <Badge variant="outline" className="shrink-0 text-xs">
                        {(ref.relevance_score * 100).toFixed(0)}%
                      </Badge>
                    )}
                  </li>
                ))}
              </ul>
            </section>
          )}
        </div>

        {/* Right column: evaluation (sticky) */}
        <div className="lg:sticky lg:top-6 lg:self-start lg:max-h-[calc(100vh-3rem)] lg:overflow-y-auto">
          <EvaluationPanel
            queryId={data.id}
            existing={data.evaluation}
            onSaved={invalidate}
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
}: {
  queryId: number;
  existing: Evaluation | null;
  onSaved: () => void;
}) {
  const [checklist, setChecklist] = useState<ChecklistState>(
    existing?.checklist ?? DEFAULT_CHECKLIST,
  );
  const [note, setNote] = useState(existing?.note ?? "");

  useEffect(() => {
    if (existing) {
      setChecklist(existing.checklist);
      setNote(existing.note ?? "");
    }
  }, [existing]);

  const mutation = useMutation({
    mutationFn: () =>
      reviewFetch(`/api/v1/review/queries/${queryId}/evaluate`, {
        method: "POST",
        body: JSON.stringify({ checklist, note: note || null }),
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

        <Button
          className="w-full"
          onClick={() => mutation.mutate()}
          disabled={mutation.isPending}
        >
          {mutation.isPending ? "Saving..." : "Save Evaluation"}
        </Button>

        {mutation.isSuccess && (
          <p className="text-sm text-green-600">Evaluation saved.</p>
        )}
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
