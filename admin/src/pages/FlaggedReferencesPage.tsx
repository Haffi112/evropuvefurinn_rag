import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { formatDistanceToNow } from "date-fns";
import {
  Check,
  CheckCheck,
  ChevronDown,
  ChevronRight,
  ExternalLink,
  Flag,
  Users,
} from "lucide-react";
import { apiFetch } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Metric } from "@/components/charts";
import { cn } from "@/lib/utils";

interface FlagEntry {
  id: number;
  reviewer_id: number;
  reviewer_username: string;
  reason: string | null;
  query_log_id: number | null;
  resolved_at: string | null;
  resolved_by_username: string | null;
  created_at: string;
}

interface ArticleFlagGroup {
  article_id: string;
  title: string;
  source_url: string;
  article_date: string;
  total_flags: number;
  open_flags: number;
  resolved_flags: number;
  distinct_reviewers: number;
  most_recent: string;
  flags: FlagEntry[];
}

interface FlagStats {
  open_flags: number;
  resolved_flags: number;
  articles_with_open_flags: number;
}

interface FlaggedResponse {
  items: ArticleFlagGroup[];
  stats: FlagStats;
}

const TABS = ["open", "resolved", "all"] as const;
type Tab = (typeof TABS)[number];

export default function FlaggedReferencesPage() {
  const [tab, setTab] = useState<Tab>("open");
  const qc = useQueryClient();

  const queryKey = ["flagged-references", tab];
  const { data, isLoading } = useQuery<FlaggedResponse>({
    queryKey,
    queryFn: () => apiFetch(`/api/v1/admin/flagged-references?resolved=${tab === "all" ? "all" : tab === "resolved" ? "true" : "false"}`),
    staleTime: 10_000,
  });

  const resolveOne = async (flagId: number) => {
    await apiFetch(`/api/v1/admin/flagged-references/${flagId}/resolve`, { method: "POST" });
    qc.invalidateQueries({ queryKey: ["flagged-references"] });
  };

  const resolveAllForArticle = async (articleId: string) => {
    await apiFetch(`/api/v1/admin/flagged-references/articles/${articleId}/resolve-all`, { method: "POST" });
    qc.invalidateQueries({ queryKey: ["flagged-references"] });
  };

  return (
    <div className="space-y-8">
      {/* Header */}
      <div>
        <p className="flex items-center gap-1.5 text-[10px] font-bold uppercase tracking-[0.12em] text-amber-600 dark:text-amber-400">
          <Flag className="h-3 w-3" />
          Flagged content
        </p>
        <h1 className="mt-1 text-3xl font-bold">Outdated references</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Articles reviewers have flagged as no longer accurate or current — ordered by open-flag count.
        </p>
      </div>

      {/* Hero metrics */}
      {data && (
        <div className="grid gap-4 sm:grid-cols-3">
          <Metric
            label="Open flags"
            value={data.stats.open_flags.toLocaleString()}
            accent="amber"
          />
          <Metric
            label="Articles affected"
            value={data.stats.articles_with_open_flags.toLocaleString()}
            hint="distinct articles with at least one open flag"
            accent="primary"
          />
          <Metric
            label="Resolved (all time)"
            value={data.stats.resolved_flags.toLocaleString()}
            accent="emerald"
          />
        </div>
      )}

      {/* Filter tabs */}
      <div className="flex gap-1 rounded-sm border bg-muted p-1 w-fit">
        {TABS.map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={cn(
              "rounded-sm px-4 py-1.5 text-sm font-medium transition-colors capitalize",
              tab === t
                ? "bg-background text-primary shadow-sm"
                : "text-muted-foreground hover:text-foreground",
            )}
          >
            {t}
          </button>
        ))}
      </div>

      {/* List */}
      {isLoading ? (
        <div className="space-y-3">
          {Array.from({ length: 3 }).map((_, i) => (
            <Skeleton key={i} className="h-24 w-full" />
          ))}
        </div>
      ) : !data || data.items.length === 0 ? (
        <EmptyState tab={tab} />
      ) : (
        <div className="space-y-3">
          {data.items.map((group) => (
            <ArticleGroup
              key={group.article_id}
              group={group}
              onResolveOne={resolveOne}
              onResolveAll={resolveAllForArticle}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function EmptyState({ tab }: { tab: Tab }) {
  const msg =
    tab === "open"
      ? "No open flags — every flagged reference has been resolved."
      : tab === "resolved"
        ? "No resolved flags yet."
        : "No references have been flagged yet.";
  return (
    <Card className="flex flex-col items-center justify-center gap-4 border-dashed py-16 text-center">
      <div className="flex h-14 w-14 items-center justify-center rounded-full bg-emerald-500/10">
        <CheckCheck className="h-7 w-7 text-emerald-600" />
      </div>
      <div className="space-y-1">
        <h3 className="text-lg font-semibold">All clear</h3>
        <p className="max-w-sm text-sm text-muted-foreground">{msg}</p>
      </div>
    </Card>
  );
}

function ArticleGroup({
  group,
  onResolveOne,
  onResolveAll,
}: {
  group: ArticleFlagGroup;
  onResolveOne: (id: number) => Promise<void>;
  onResolveAll: (articleId: string) => Promise<void>;
}) {
  const [expanded, setExpanded] = useState(group.open_flags > 0);
  const [busy, setBusy] = useState(false);

  const handleResolveAll = async () => {
    if (!confirm(`Resolve all ${group.open_flags} open flags on this article?`)) return;
    setBusy(true);
    try {
      await onResolveAll(group.article_id);
    } finally {
      setBusy(false);
    }
  };

  const hasOpen = group.open_flags > 0;

  return (
    <Card
      className={cn(
        "overflow-hidden transition-colors",
        hasOpen ? "border-l-4 border-l-amber-500" : "border-l-4 border-l-emerald-500/60",
      )}
    >
      {/* Header */}
      <div className="flex items-start gap-3 p-4">
        <button
          onClick={() => setExpanded((v) => !v)}
          className="mt-1 shrink-0 text-muted-foreground hover:text-foreground"
          aria-label={expanded ? "Collapse" : "Expand"}
        >
          {expanded ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
        </button>

        <div className="min-w-0 flex-1">
          <div className="flex items-baseline gap-2">
            <h3 className="truncate text-base font-semibold">{group.title}</h3>
            <span className="shrink-0 font-mono text-[10px] text-muted-foreground">
              {group.article_id}
            </span>
          </div>
          <a
            href={group.source_url}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1 text-xs text-blue-600 hover:underline"
          >
            {group.source_url}
            <ExternalLink className="h-3 w-3" />
          </a>
        </div>

        <div className="flex shrink-0 items-center gap-2">
          {hasOpen ? (
            <span className="inline-flex items-center gap-1.5 rounded-full bg-amber-500/15 px-3 py-1 text-sm font-semibold text-amber-700 tabular-nums dark:text-amber-300">
              <Flag className="h-3.5 w-3.5" />
              {group.open_flags} open
            </span>
          ) : (
            <span className="inline-flex items-center gap-1.5 rounded-full bg-emerald-500/15 px-3 py-1 text-sm font-semibold text-emerald-700 tabular-nums dark:text-emerald-300">
              <Check className="h-3.5 w-3.5" />
              Resolved
            </span>
          )}
          {group.distinct_reviewers > 0 && hasOpen && (
            <span
              className="inline-flex items-center gap-1 rounded-full border bg-background px-2 py-1 text-xs text-muted-foreground tabular-nums"
              title={`${group.distinct_reviewers} distinct reviewers flagged this`}
            >
              <Users className="h-3 w-3" />
              {group.distinct_reviewers}
            </span>
          )}
        </div>
      </div>

      {/* Details */}
      {expanded && (
        <div className="border-t bg-muted/20 px-4 py-3">
          <div className="mb-3 flex items-center justify-between">
            <p className="text-[10px] font-bold uppercase tracking-[0.1em] text-muted-foreground">
              Individual flags · {group.total_flags} total ({group.open_flags} open, {group.resolved_flags} resolved)
            </p>
            {hasOpen && (
              <Button
                size="sm"
                variant="outline"
                onClick={handleResolveAll}
                disabled={busy}
                className="h-7 text-xs"
              >
                <CheckCheck className="mr-1.5 h-3 w-3" />
                Resolve all {group.open_flags}
              </Button>
            )}
          </div>

          <ul className="divide-y divide-border/60">
            {group.flags.map((f) => (
              <FlagRow key={f.id} flag={f} onResolve={onResolveOne} />
            ))}
          </ul>
        </div>
      )}
    </Card>
  );
}

function FlagRow({
  flag,
  onResolve,
}: {
  flag: FlagEntry;
  onResolve: (id: number) => Promise<void>;
}) {
  const [busy, setBusy] = useState(false);
  const isOpen = !flag.resolved_at;

  const handleResolve = async () => {
    setBusy(true);
    try {
      await onResolve(flag.id);
    } finally {
      setBusy(false);
    }
  };

  return (
    <li className="flex items-start gap-3 py-2.5">
      <div
        className={cn(
          "mt-1 h-1.5 w-1.5 shrink-0 rounded-full",
          isOpen ? "bg-amber-500" : "bg-emerald-500/60",
        )}
      />
      <div className="min-w-0 flex-1 space-y-0.5">
        <div className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5 text-sm">
          <span className="font-medium">{flag.reviewer_username}</span>
          <span className="text-xs text-muted-foreground">
            {formatDistanceToNow(new Date(flag.created_at), { addSuffix: true })}
          </span>
          {flag.query_log_id != null && (
            <a
              href={`/review/queries/${flag.query_log_id}`}
              target="_blank"
              rel="noopener noreferrer"
              className="text-xs text-blue-600 hover:underline"
            >
              query #{flag.query_log_id}
            </a>
          )}
          {!isOpen && (
            <span className="text-xs text-emerald-600 dark:text-emerald-400">
              resolved{flag.resolved_by_username ? ` by ${flag.resolved_by_username}` : ""}{" "}
              {flag.resolved_at
                ? formatDistanceToNow(new Date(flag.resolved_at), { addSuffix: true })
                : ""}
            </span>
          )}
        </div>
        {flag.reason && (
          <p className="text-sm italic text-muted-foreground">"{flag.reason}"</p>
        )}
      </div>
      {isOpen && (
        <Button
          size="sm"
          variant="ghost"
          onClick={handleResolve}
          disabled={busy}
          className="h-7 shrink-0 px-2 text-xs text-emerald-700 hover:bg-emerald-500/10 dark:text-emerald-300"
        >
          <Check className="mr-1 h-3 w-3" />
          Resolve
        </Button>
      )}
    </li>
  );
}
