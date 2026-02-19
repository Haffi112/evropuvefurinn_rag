import { Fragment, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { formatDistanceToNow } from "date-fns";
import {
  AlertCircle,
  Check,
  ChevronDown,
  ChevronRight,
  Database,
  Download,
  FileArchive,
  Minus,
  Search,
  X,
} from "lucide-react";
import { apiFetch, getApiKey } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
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

interface EvaluationChecklist {
  answers_question: boolean;
  factually_accurate: boolean;
  sources_relevant: boolean;
  no_hallucinations: boolean;
  appropriate_scope: boolean;
  language_quality: boolean;
}

interface AdminEvaluation {
  id: number;
  query_log_id: number;
  query_text: string;
  reviewer_username: string;
  checklist: EvaluationChecklist;
  note: string | null;
  review_status: string;
  has_article: boolean;
  evaluation_date: string;
  evaluation_updated: string | null;
  query_date: string;
}

interface AdminEvaluationList {
  evaluations: AdminEvaluation[];
  total: number;
  page: number;
  per_page: number;
  total_pages: number;
}

const CHECKLIST_LABELS: Record<keyof EvaluationChecklist, string> = {
  answers_question: "Answers question",
  factually_accurate: "Factually accurate",
  sources_relevant: "Sources relevant",
  no_hallucinations: "No hallucinations",
  appropriate_scope: "Appropriate scope",
  language_quality: "Language quality",
};

function checklistScore(cl: EvaluationChecklist): number {
  return Object.values(cl).filter(Boolean).length;
}

function statusVariant(status: string) {
  switch (status) {
    case "approved":
      return "default" as const;
    case "reviewed":
      return "secondary" as const;
    default:
      return "outline" as const;
  }
}

function downloadFile(url: string, filename: string) {
  const key = getApiKey();
  fetch(url, {
    headers: key ? { "X-API-Key": key } : {},
  })
    .then((res) => res.blob())
    .then((blob) => {
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = filename;
      a.click();
      URL.revokeObjectURL(a.href);
    });
}

export default function ReviewsPage() {
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState<string>("all");
  const [expanded, setExpanded] = useState<Set<number>>(new Set());

  const params = new URLSearchParams();
  params.set("page", String(page));
  params.set("per_page", "30");
  if (search) params.set("search", search);
  if (statusFilter !== "all") params.set("review_status", statusFilter);

  const evals = useQuery<AdminEvaluationList>({
    queryKey: ["admin-reviews", page, search, statusFilter],
    queryFn: () => apiFetch(`/api/v1/admin/reviews?${params}`),
  });

  function toggleExpand(id: number) {
    setExpanded((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h1 className="text-3xl font-bold">Reviews</h1>
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={() =>
              downloadFile("/api/v1/admin/reviews/export/csv", "evaluations.csv")
            }
          >
            <Download className="mr-1.5 h-3.5 w-3.5" />
            Export CSV
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={() =>
              downloadFile(
                "/api/v1/admin/reviews/export/articles",
                "reviewed_articles.zip",
              )
            }
          >
            <FileArchive className="mr-1.5 h-3.5 w-3.5" />
            Export Articles
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={() =>
              downloadFile(
                "/api/v1/admin/reviews/export/all",
                "evropuvefur_all_data.zip",
              )
            }
          >
            <Database className="mr-1.5 h-3.5 w-3.5" />
            Export All Data
          </Button>
        </div>
      </div>

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
          value={statusFilter}
          onValueChange={(v) => {
            setStatusFilter(v);
            setPage(1);
          }}
        >
          <SelectTrigger className="w-36">
            <SelectValue placeholder="Status" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All</SelectItem>
            <SelectItem value="reviewed">Reviewed</SelectItem>
            <SelectItem value="approved">Approved</SelectItem>
            <SelectItem value="pending">Pending</SelectItem>
          </SelectContent>
        </Select>
      </div>

      {/* Table */}
      {evals.isLoading ? (
        <div className="space-y-2">
          {Array.from({ length: 10 }).map((_, i) => (
            <Skeleton key={i} className="h-10 w-full" />
          ))}
        </div>
      ) : evals.isError ? (
        <div className="flex flex-col items-center justify-center gap-2 rounded-lg border border-destructive/50 bg-destructive/5 py-12">
          <AlertCircle className="h-8 w-8 text-destructive" />
          <p className="text-sm font-medium text-destructive">
            Failed to load evaluations
          </p>
          <p className="text-xs text-muted-foreground">
            {evals.error instanceof Error ? evals.error.message : "An unexpected error occurred"}
          </p>
          <Button variant="outline" size="sm" onClick={() => evals.refetch()} className="mt-2">
            Try again
          </Button>
        </div>
      ) : (
        <>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-8" />
                <TableHead>Query</TableHead>
                <TableHead>Reviewer</TableHead>
                <TableHead>Score</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Article</TableHead>
                <TableHead>Date</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {evals.data?.evaluations.map((ev) => {
                const score = checklistScore(ev.checklist);
                return (
                  <Fragment key={ev.id}>
                    <TableRow
                      className="cursor-pointer"
                      onClick={() => toggleExpand(ev.id)}
                    >
                      <TableCell>
                        {expanded.has(ev.id) ? (
                          <ChevronDown className="h-4 w-4" />
                        ) : (
                          <ChevronRight className="h-4 w-4" />
                        )}
                      </TableCell>
                      <TableCell className="max-w-xs truncate font-mono text-xs">
                        {ev.query_text}
                      </TableCell>
                      <TableCell className="text-sm">{ev.reviewer_username}</TableCell>
                      <TableCell>
                        <span
                          className={`text-sm font-bold ${score === 6 ? "text-green-600" : score >= 4 ? "text-yellow-600" : "text-red-600"}`}
                        >
                          {score}/6
                        </span>
                      </TableCell>
                      <TableCell>
                        <Badge variant={statusVariant(ev.review_status)} className="text-xs">
                          {ev.review_status}
                        </Badge>
                      </TableCell>
                      <TableCell>
                        {ev.has_article ? (
                          <Check className="h-4 w-4 text-green-600" />
                        ) : (
                          <Minus className="h-4 w-4 text-muted-foreground" />
                        )}
                      </TableCell>
                      <TableCell className="text-xs text-muted-foreground">
                        {formatDistanceToNow(new Date(ev.evaluation_date), {
                          addSuffix: true,
                        })}
                      </TableCell>
                    </TableRow>
                    {expanded.has(ev.id) && (
                      <TableRow key={`${ev.id}-detail`}>
                        <TableCell colSpan={7} className="bg-secondary/50 p-4 detail-accent">
                          <div className="space-y-3 text-sm">
                            <div className="grid grid-cols-2 gap-x-8 gap-y-1 sm:grid-cols-3">
                              {(
                                Object.entries(CHECKLIST_LABELS) as [
                                  keyof EvaluationChecklist,
                                  string,
                                ][]
                              ).map(([key, label]) => (
                                <div key={key} className="flex items-center gap-2">
                                  {ev.checklist[key] ? (
                                    <Check className="h-4 w-4 text-green-600" />
                                  ) : (
                                    <X className="h-4 w-4 text-red-500" />
                                  )}
                                  <span
                                    className={
                                      ev.checklist[key]
                                        ? "text-foreground"
                                        : "text-muted-foreground"
                                    }
                                  >
                                    {label}
                                  </span>
                                </div>
                              ))}
                            </div>
                            {ev.note && (
                              <p className="text-muted-foreground">
                                <span className="font-medium text-foreground">Note: </span>
                                {ev.note}
                              </p>
                            )}
                          </div>
                        </TableCell>
                      </TableRow>
                    )}
                  </Fragment>
                );
              })}
              {(!evals.data || evals.data.evaluations.length === 0) && (
                <TableRow>
                  <TableCell colSpan={7} className="text-center text-muted-foreground">
                    No evaluations found
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>

          {evals.data && evals.data.total_pages > 1 && (
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
                Page {evals.data.page} of {evals.data.total_pages}
              </span>
              <Button
                variant="outline"
                size="sm"
                disabled={page >= evals.data.total_pages}
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
