import { BookOpen, Globe } from "lucide-react";

type Mode = "rag" | "websearch" | string;

interface ModeBadgeProps {
  mode: Mode | null | undefined;
  size?: "sm" | "md";
}

export default function ModeBadge({ mode, size = "sm" }: ModeBadgeProps) {
  const isWeb = mode === "websearch";
  const Icon = isWeb ? Globe : BookOpen;
  const label = isWeb ? "Web" : "RAG";
  const title = isWeb
    ? "Answered via web search"
    : "Answered via knowledge base (RAG)";

  const base =
    "inline-flex items-center gap-1 rounded-full border font-medium tabular-nums";
  const sizing =
    size === "md" ? "px-2.5 py-0.5 text-xs" : "px-2 py-0.5 text-[11px]";
  const theme = isWeb
    ? "border-sky-500/30 bg-sky-500/10 text-sky-700 dark:text-sky-300"
    : "border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-300";

  return (
    <span className={`${base} ${sizing} ${theme}`} title={title} aria-label={title}>
      <Icon className={size === "md" ? "h-3.5 w-3.5" : "h-3 w-3"} />
      {label}
    </span>
  );
}
