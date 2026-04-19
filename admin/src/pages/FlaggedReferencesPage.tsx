import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { formatDistanceToNow } from "date-fns";
import {
  BookOpen,
  Check,
  CheckCheck,
  ChevronDown,
  ChevronRight,
  ExternalLink,
  Flag,
  Globe,
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
  flag_type: "outdated" | "irrelevant" | "untrustworthy";
  reviewer_id: number;
  reviewer_username: string;
  reason: string | null;
  query_log_id: number | null;
  resolved_at: string | null;
  resolved_by_username: string | null;
  created_at: string;
}

interface FlagGroup {
  kind: "article" | "url";
  article_id: string | null;
  url: string | null;
  article_title: string | null;
  article_source_url: string | null;
  article_date: string | null;
  total_flags: number;
  open_flags: number;
  resolved_flags: number;
  distinct_reviewers: number;
  most_recent: string;
  open_outdated: number;
  open_irrelevant: number;
  open_untrustworthy: number;
  flags: FlagEntry[];
}

interface DomainStat {
  domain: string;
  total_flags: number;
  open_flags: number;
  resolved_flags: number;
  open_irrelevant: number;
  open_untrustworthy: number;
  open_outdated: number;
  distinct_urls: number;
}

interface FlagStats {
  open_flags: number;
  resolved_flags: number;
  articles_with_open_flags: number;
  urls_with_open_flags: number;
  open_outdated: number;
  open_irrelevant: number;
  open_untrustworthy: number;
}

interface FlaggedResponse {
  items: FlagGroup[];
  domains: DomainStat[];
  stats: FlagStats;
}

const TABS = ["open", "resolved", "all"] as const;
type Tab = (typeof TABS)[number];
const KIND_FILTERS = ["all", "article", "url"] as const;
type KindFilter = (typeof KIND_FILTERS)[number];

const FLAG_TYPE_META = {
  outdated: { label: "Outdated", color: "rgb(245 158 11)" },
  irrelevant: { label: "Irrelevant", color: "rgb(14 165 233)" },
  untrustworthy: { label: "Untrustworthy", color: "rgb(239 68 68)" },
} as const;

function flagTypeLabel(t: string): string {
  return FLAG_TYPE_META[t as keyof typeof FLAG_TYPE_META]?.label ?? t;
}

function domainOf(url: string): string {
  try {
    const u = new URL(url);
    return u.hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}

export default function FlaggedReferencesPage() {
  const [tab, setTab] = useState<Tab>("open");
  const [kind, setKind] = useState<KindFilter>("all");
  const qc = useQueryClient();

  const { data, isLoading } = useQuery<FlaggedResponse>({
    queryKey: ["flagged-references", tab],
    queryFn: () =>
      apiFetch(
        `/api/v1/admin/flagged-references?resolved=${
          tab === "all" ? "all" : tab === "resolved" ? "true" : "false"
        }`,
      ),
    staleTime: 10_000,
  });

  const resolveOne = async (flagId: number) => {
    await apiFetch(`/api/v1/admin/flagged-references/${flagId}/resolve`, {
      method: "POST",
    });
    qc.invalidateQueries({ queryKey: ["flagged-references"] });
  };

  const resolveAllForArticle = async (articleId: string) => {
    await apiFetch(
      `/api/v1/admin/flagged-references/articles/${articleId}/resolve-all`,
      { method: "POST" },
    );
    qc.invalidateQueries({ queryKey: ["flagged-references"] });
  };

  const resolveAllForUrl = async (url: string) => {
    await apiFetch(`/api/v1/admin/flagged-references/urls/resolve-all`, {
      method: "POST",
      body: JSON.stringify({ url }),
    });
    qc.invalidateQueries({ queryKey: ["flagged-references"] });
  };

  const filteredItems = (data?.items ?? []).filter(
    (it) => kind === "all" || it.kind === kind,
  );

  return (
    <div className="space-y-8">
      {/* Header */}
      <div>
        <p className="flex items-center gap-1.5 text-[10px] font-bold uppercase tracking-[0.12em] text-amber-600 dark:text-amber-400">
          <Flag className="h-3 w-3" />
          Flagged content
        </p>
        <h1 className="mt-1 text-3xl font-bold">Flagged references</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          RAG articles and web sources that reviewers flagged — ordered by
          open-flag count. Web sources are also aggregated by domain below.
        </p>
      </div>

      {/* Hero metrics */}
      {data && (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <Metric
            label="Open flags"
            value={data.stats.open_flags.toLocaleString()}
            hint={`${data.stats.open_outdated} outdated · ${data.stats.open_irrelevant} irrelevant · ${data.stats.open_untrustworthy} untrustworthy`}
            accent="amber"
          />
          <Metric
            label="Articles"
            value={data.stats.articles_with_open_flags.toLocaleString()}
            hint="RAG articles with open flags"
            accent="primary"
          />
          <Metric
            label="Web sources"
            value={data.stats.urls_with_open_flags.toLocaleString()}
            hint="distinct URLs with open flags"
            accent="sky"
          />
          <Metric
            label="Resolved"
            value={data.stats.resolved_flags.toLocaleString()}
            hint="all time"
            accent="emerald"
          />
        </div>
      )}

      {/* Domain aggregate */}
      {data && data.domains.length > 0 && (
        <Card className="p-5">
          <div className="mb-4 flex items-baseline justify-between">
            <h2 className="flex items-center gap-2 text-sm font-bold uppercase tracking-wider text-muted-foreground">
              <Globe className="h-3.5 w-3.5" />
              Top flagged domains
            </h2>
            <span className="text-xs text-muted-foreground">
              {data.domains.length} domain{data.domains.length === 1 ? "" : "s"}
            </span>
          </div>
          <ul className="divide-y">
            {data.domains.slice(0, 10).map((d) => (
              <li
                key={d.domain}
                className="flex items-center gap-3 py-2 text-sm"
              >
                <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-sky-500/10">
                  <Globe className="h-4 w-4 text-sky-600 dark:text-sky-400" />
                </div>
                <div className="min-w-0 flex-1">
                  <p className="truncate font-mono font-medium">
                    {d.domain}
                  </p>
                  <p className="text-xs text-muted-foreground">
                    {d.distinct_urls} URL{d.distinct_urls === 1 ? "" : "s"}
                  </p>
                </div>
                <FlagTypeChips
                  irrelevant={d.open_irrelevant}
                  untrustworthy={d.open_untrustworthy}
                  outdated={d.open_outdated}
                />
                <span className="inline-flex w-16 shrink-0 items-center justify-end gap-1 rounded-full bg-amber-500/15 px-3 py-1 text-sm font-semibold text-amber-700 tabular-nums dark:text-amber-300">
                  <Flag className="h-3 w-3" />
                  {d.open_flags}
                </span>
              </li>
            ))}
          </ul>
        </Card>
      )}

      {/* Filter tabs */}
      <div className="flex flex-wrap items-center gap-3">
        <div className="flex gap-1 rounded-sm border bg-muted p-1">
          {TABS.map((t) => (
            <button
              key={t}
              onClick={() => setTab(t)}
              className={cn(
                "rounded-sm px-4 py-1.5 text-sm font-medium capitalize transition-colors",
                tab === t
                  ? "bg-background text-primary shadow-sm"
                  : "text-muted-foreground hover:text-foreground",
              )}
            >
              {t}
            </button>
          ))}
        </div>

        <div className="flex gap-1 rounded-sm border bg-muted p-1">
          {KIND_FILTERS.map((k) => (
            <button
              key={k}
              onClick={() => setKind(k)}
              className={cn(
                "rounded-sm px-3 py-1.5 text-sm font-medium capitalize transition-colors",
                kind === k
                  ? "bg-background text-primary shadow-sm"
                  : "text-muted-foreground hover:text-foreground",
              )}
            >
              {k === "article" ? "RAG" : k === "url" ? "Web" : "All"}
            </button>
          ))}
        </div>
      </div>

      {/* List */}
      {isLoading ? (
        <div className="space-y-3">
          {Array.from({ length: 3 }).map((_, i) => (
            <Skeleton key={i} className="h-24 w-full" />
          ))}
        </div>
      ) : filteredItems.length === 0 ? (
        <EmptyState tab={tab} />
      ) : (
        <div className="space-y-3">
          {filteredItems.map((group) => (
            <FlagGroupCard
              key={`${group.kind}:${group.article_id ?? group.url}`}
              group={group}
              onResolveOne={resolveOne}
              onResolveAll={
                group.kind === "article"
                  ? () => resolveAllForArticle(group.article_id!)
                  : () => resolveAllForUrl(group.url!)
              }
            />
          ))}
        </div>
      )}
    </div>
  );
}

function FlagTypeChips({
  outdated,
  irrelevant,
  untrustworthy,
}: {
  outdated: number;
  irrelevant: number;
  untrustworthy: number;
}) {
  const any = outdated + irrelevant + untrustworthy > 0;
  if (!any) return null;
  return (
    <div className="flex shrink-0 items-center gap-1.5 text-[11px] font-medium tabular-nums">
      {outdated > 0 && (
        <span
          className="inline-flex items-center gap-1 rounded-full bg-amber-500/10 px-1.5 py-0.5 text-amber-700 dark:text-amber-300"
          title={`${outdated} outdated`}
        >
          <span className="h-1.5 w-1.5 rounded-full bg-amber-500" />
          {outdated}
        </span>
      )}
      {irrelevant > 0 && (
        <span
          className="inline-flex items-center gap-1 rounded-full bg-sky-500/10 px-1.5 py-0.5 text-sky-700 dark:text-sky-300"
          title={`${irrelevant} irrelevant`}
        >
          <span className="h-1.5 w-1.5 rounded-full bg-sky-500" />
          {irrelevant}
        </span>
      )}
      {untrustworthy > 0 && (
        <span
          className="inline-flex items-center gap-1 rounded-full bg-red-500/10 px-1.5 py-0.5 text-red-700 dark:text-red-300"
          title={`${untrustworthy} untrustworthy`}
        >
          <span className="h-1.5 w-1.5 rounded-full bg-red-500" />
          {untrustworthy}
        </span>
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

function FlagGroupCard({
  group,
  onResolveOne,
  onResolveAll,
}: {
  group: FlagGroup;
  onResolveOne: (id: number) => Promise<void>;
  onResolveAll: () => Promise<void>;
}) {
  const [expanded, setExpanded] = useState(group.open_flags > 0);
  const [busy, setBusy] = useState(false);

  const handleResolveAll = async () => {
    if (!confirm(`Resolve all ${group.open_flags} open flags?`)) return;
    setBusy(true);
    try {
      await onResolveAll();
    } finally {
      setBusy(false);
    }
  };

  const hasOpen = group.open_flags > 0;
  const isArticle = group.kind === "article";
  const displayTitle =
    isArticle && group.article_title ? group.article_title : group.url ?? "";
  const displayUrl =
    isArticle && group.article_source_url
      ? group.article_source_url
      : group.url ?? "";
  const Icon = isArticle ? BookOpen : Globe;
  const iconColor = isArticle ? "text-amber-600" : "text-sky-600";
  const iconBg = isArticle ? "bg-amber-500/10" : "bg-sky-500/10";
  const kindLabel = isArticle ? "RAG" : "Web";

  return (
    <Card
      className={cn(
        "overflow-hidden transition-colors",
        hasOpen
          ? "border-l-4 border-l-amber-500"
          : "border-l-4 border-l-emerald-500/60",
      )}
    >
      {/* Header */}
      <div className="flex items-start gap-3 p-4">
        <button
          onClick={() => setExpanded((v) => !v)}
          className="mt-1 shrink-0 text-muted-foreground hover:text-foreground"
          aria-label={expanded ? "Collapse" : "Expand"}
        >
          {expanded ? (
            <ChevronDown className="h-4 w-4" />
          ) : (
            <ChevronRight className="h-4 w-4" />
          )}
        </button>

        <div className={cn("mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-md", iconBg)}>
          <Icon className={cn("h-4 w-4", iconColor)} />
        </div>

        <div className="min-w-0 flex-1">
          <div className="flex items-baseline gap-2">
            <span className="rounded-sm border bg-background px-1.5 py-0.5 text-[10px] font-bold uppercase tracking-wider text-muted-foreground">
              {kindLabel}
            </span>
            <h3 className="truncate text-base font-semibold">
              {displayTitle || "Untitled"}
            </h3>
            {isArticle && group.article_id && (
              <span className="shrink-0 font-mono text-[10px] text-muted-foreground">
                {group.article_id}
              </span>
            )}
          </div>
          {displayUrl && (
            <a
              href={displayUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1 text-xs text-blue-600 hover:underline"
            >
              {isArticle ? displayUrl : domainOf(displayUrl)}
              <ExternalLink className="h-3 w-3" />
            </a>
          )}
        </div>

        <div className="flex shrink-0 flex-col items-end gap-1.5">
          <div className="flex items-center gap-2">
            <FlagTypeChips
              outdated={group.open_outdated}
              irrelevant={group.open_irrelevant}
              untrustworthy={group.open_untrustworthy}
            />
            {hasOpen ? (
              <span className="inline-flex items-center gap-1.5 rounded-full bg-amber-500/15 px-3 py-1 text-sm font-semibold text-amber-700 tabular-nums dark:text-amber-300">
                <Flag className="h-3.5 w-3.5" />
                {group.open_flags} open
              </span>
            ) : (
              <span className="inline-flex items-center gap-1.5 rounded-full bg-emerald-500/15 px-3 py-1 text-sm font-semibold text-emerald-700 dark:text-emerald-300">
                <Check className="h-3.5 w-3.5" />
                Resolved
              </span>
            )}
          </div>
          {group.distinct_reviewers > 0 && hasOpen && (
            <span
              className="inline-flex items-center gap-1 rounded-full border bg-background px-2 py-0.5 text-xs text-muted-foreground tabular-nums"
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
              Individual flags · {group.total_flags} total ({group.open_flags} open,{" "}
              {group.resolved_flags} resolved)
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
  const typeColor = FLAG_TYPE_META[flag.flag_type]?.color ?? "rgb(100 116 139)";

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
          "mt-1.5 h-2 w-2 shrink-0 rounded-full",
          isOpen ? "" : "opacity-40",
        )}
        style={{ backgroundColor: typeColor }}
      />
      <div className="min-w-0 flex-1 space-y-0.5">
        <div className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5 text-sm">
          <span className="font-medium">{flag.reviewer_username}</span>
          <span
            className="rounded-full px-1.5 py-0 text-[10px] font-semibold uppercase tracking-wider"
            style={{
              backgroundColor: typeColor + "22",
              color: typeColor,
            }}
          >
            {flagTypeLabel(flag.flag_type)}
          </span>
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
              resolved
              {flag.resolved_by_username ? ` by ${flag.resolved_by_username}` : ""}{" "}
              {flag.resolved_at
                ? formatDistanceToNow(new Date(flag.resolved_at), {
                    addSuffix: true,
                  })
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
