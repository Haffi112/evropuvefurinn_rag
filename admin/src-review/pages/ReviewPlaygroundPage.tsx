import { useCallback, useRef, useState } from "react";
import { ExternalLink, Globe, Loader2, Send } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { getToken } from "@review/lib/review-api";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

interface Reference {
  number: number;
  id: string;
  title: string;
  source_url: string;
  date: string;
  relevance_score: number;
}

export default function ReviewPlaygroundPage() {
  const [query, setQuery] = useState("");
  const [language, setLanguage] = useState("auto");
  const [streaming, setStreaming] = useState(true);
  const [webSearch, setWebSearch] = useState(false);
  const [modelChoice, setModelChoice] = useState<string>("auto");
  const [answer, setAnswer] = useState("");
  const [refs, setRefs] = useState<Reference[]>([]);
  const [meta, setMeta] = useState<Record<string, unknown>>({});
  const [loading, setLoading] = useState(false);
  const [status, setStatus] = useState("");
  const [thinking, setThinking] = useState("");
  const [scoreThreshold, setScoreThreshold] = useState<number | null>(null);
  const [maxArticles, setMaxArticles] = useState(5);
  const [showThinking, setShowThinking] = useState(false);
  const abortRef = useRef<AbortController | null>(null);

  const handleSubmit = useCallback(async () => {
    if (!query.trim() || loading) return;
    setAnswer("");
    setThinking("");
    setRefs([]);
    setMeta({});
    setStatus("");
    setLoading(true);

    const controller = new AbortController();
    abortRef.current = controller;

    const token = getToken();

    try {
      const body = {
        query,
        stream: streaming,
        language,
        web_search: webSearch,
        include_thinking: showThinking,
        model: modelChoice === "auto" ? null : modelChoice,
        ...(webSearch ? {} : { top_k: maxArticles, score_threshold: scoreThreshold }),
      };

      if (streaming) {
        const res = await fetch("/api/v1/review/playground", {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            Authorization: `Bearer ${token ?? ""}`,
          },
          body: JSON.stringify(body),
          signal: controller.signal,
        });

        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const reader = res.body?.getReader();
        if (!reader) throw new Error("No reader");

        const decoder = new TextDecoder();
        let buffer = "";
        let currentEvent = "";

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });

          const lines = buffer.split("\n");
          buffer = lines.pop() ?? "";

          for (const line of lines) {
            if (line.startsWith("event:")) {
              currentEvent = line.slice(6).trim();
            } else if (line.startsWith("data:")) {
              const raw = line.slice(5).trim();
              if (raw && currentEvent) {
                try {
                  handleSSEEvent(currentEvent, JSON.parse(raw));
                } catch { /* skip malformed JSON */ }
              }
            } else if (line.trim() === "") {
              currentEvent = "";
            }
          }
        }
      } else {
        const res = await fetch("/api/v1/review/playground", {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            Authorization: `Bearer ${token ?? ""}`,
          },
          body: JSON.stringify(body),
          signal: controller.signal,
        });
        const data = await res.json();
        setAnswer(data.answer ?? "");
        setRefs(data.references ?? []);
        setMeta({
          model_used: data.model_used,
          cached: data.cached,
          query_id: data.query_id,
          scope_declined: data.scope_declined,
        });
      }
    } catch (err) {
      if ((err as Error).name !== "AbortError") {
        setAnswer(`Error: ${(err as Error).message}`);
      }
    } finally {
      setLoading(false);
      abortRef.current = null;
    }
  }, [query, language, streaming, webSearch, modelChoice, loading, maxArticles, scoreThreshold, showThinking]);

  function handleSSEEvent(event: string, data: Record<string, unknown>) {
    switch (event) {
      case "status":
        setStatus(data.message as string);
        break;
      case "thinking":
        setThinking((prev) => prev + (data.text as string));
        break;
      case "token":
        setAnswer((prev) => prev + (data.text as string));
        break;
      case "references":
        setRefs(data.references as Reference[]);
        break;
      case "context":
        setStatus(
          `Found ${data.articles_found} articles (top score: ${data.top_score})`,
        );
        break;
      case "done":
        setMeta(data);
        setStatus("");
        break;
    }
  }

  function handleStop() {
    abortRef.current?.abort();
    setLoading(false);
  }

  return (
    <div className="space-y-6">
      <h1 className="text-3xl font-bold">Playground</h1>
      <p className="text-sm text-muted-foreground">
        Queries submitted here are logged with your name and added to the review queue.
      </p>

      <Card>
        <CardContent className="space-y-4 pt-6">
          <Textarea
            placeholder="Ask a question about the EU, EEA, or Iceland's European relations..."
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            rows={3}
            onKeyDown={(e) => {
              if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) handleSubmit();
            }}
          />
          <div className="flex items-center gap-3">
            <Select value={language} onValueChange={setLanguage}>
              <SelectTrigger className="w-28">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="auto">Auto</SelectItem>
                <SelectItem value="is">Icelandic</SelectItem>
                <SelectItem value="en">English</SelectItem>
              </SelectContent>
            </Select>
            <Select value={modelChoice} onValueChange={setModelChoice}>
              <SelectTrigger className="w-44">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="auto">Auto (quota)</SelectItem>
                <SelectItem value="pro">Gemini 3.1 Pro</SelectItem>
                <SelectItem value="flash">Gemini 3 Flash</SelectItem>
              </SelectContent>
            </Select>
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={streaming}
                onChange={(e) => setStreaming(e.target.checked)}
                className="rounded"
              />
              Stream
            </label>
            <label className="flex items-center gap-2 text-sm font-medium text-primary">
              <input
                type="checkbox"
                checked={webSearch}
                onChange={(e) => setWebSearch(e.target.checked)}
                className="rounded"
              />
              <Globe className="h-4 w-4" />
              Web Search
            </label>
            <div className="flex-1" />
            {loading ? (
              <Button variant="destructive" size="sm" onClick={handleStop}>
                Stop
              </Button>
            ) : (
              <Button size="sm" onClick={handleSubmit} disabled={!query.trim()}>
                <Send className="mr-2 h-4 w-4" />
                Send
              </Button>
            )}
          </div>

          {/* RAG-specific controls — hidden in web search mode */}
          {!webSearch && (
            <div className="flex flex-wrap items-center gap-4 border-t pt-3">
              <label className="flex items-center gap-2 text-sm">
                <span className="text-muted-foreground">Threshold:</span>
                <input
                  type="checkbox"
                  checked={scoreThreshold === null}
                  onChange={(e) =>
                    setScoreThreshold(e.target.checked ? null : 0.5)
                  }
                  className="rounded"
                />
                <span className="text-xs text-muted-foreground">Default</span>
                {scoreThreshold !== null && (
                  <>
                    <input
                      type="range"
                      min={0}
                      max={1}
                      step={0.05}
                      value={scoreThreshold}
                      onChange={(e) =>
                        setScoreThreshold(parseFloat(e.target.value))
                      }
                      className="w-24"
                    />
                    <span className="w-8 text-xs tabular-nums">
                      {scoreThreshold.toFixed(2)}
                    </span>
                  </>
                )}
              </label>
              <label className="flex items-center gap-2 text-sm">
                <span className="text-muted-foreground">Max articles:</span>
                <input
                  type="number"
                  min={1}
                  max={20}
                  value={maxArticles}
                  onChange={(e) => setMaxArticles(Number(e.target.value))}
                  className="w-16 rounded border px-2 py-0.5 text-sm"
                />
              </label>
              <label className="flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  checked={showThinking}
                  onChange={(e) => setShowThinking(e.target.checked)}
                  className="rounded"
                />
                Include thinking
              </label>
            </div>
          )}

          {webSearch && (
            <div className="rounded border border-primary/20 bg-primary/5 px-3 py-2 text-sm text-muted-foreground">
              <Globe className="mr-1 inline h-3.5 w-3.5 text-primary" />
              Web search mode: The model will search the web and cite URLs inline. Knowledge base articles are not used.
            </div>
          )}
        </CardContent>
      </Card>

      {/* Status */}
      {(loading || status) && (
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          {loading && <Loader2 className="h-4 w-4 animate-spin" />}
          <span>{status || "Processing..."}</span>
        </div>
      )}

      {/* Thinking trace */}
      {thinking && (
        <details className="rounded-sm border bg-secondary/30 p-4">
          <summary className="cursor-pointer text-sm font-medium">
            Thinking trace ({thinking.length} chars)
          </summary>
          <pre className="mt-2 whitespace-pre-wrap text-xs text-muted-foreground">
            {thinking}
          </pre>
        </details>
      )}

      {/* Answer */}
      {answer && (
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base">
              Response
              {!!meta.model_used && (
                <Badge variant="outline">{String(meta.model_used)}</Badge>
              )}
              {!!meta.cached && <Badge variant="secondary">cached</Badge>}
              {!!meta.scope_declined && (
                <Badge variant="destructive">out of scope</Badge>
              )}
              {webSearch && <Badge variant="secondary">web search</Badge>}
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="prose prose-sm max-w-none dark:prose-invert">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>
                {answer}
              </ReactMarkdown>
            </div>
          </CardContent>
        </Card>
      )}

      {/* References (RAG mode only) */}
      {refs.length > 0 && (
        <div className="space-y-2">
          <h3 className="text-sm font-medium text-muted-foreground">
            References ({refs.length})
          </h3>
          <div className="grid gap-2 sm:grid-cols-2">
            {refs.map((ref) => (
              <Card key={ref.id} className="p-3 border-b-2 border-b-primary/50">
                <div className="flex items-start justify-between gap-2">
                  <div>
                    <p className="text-sm font-medium">
                      <span className="mr-2 inline-flex h-5 w-5 items-center justify-center rounded-full bg-primary/10 text-xs font-semibold text-primary tabular-nums">
                        {ref.number}
                      </span>
                      {ref.title}
                    </p>
                    <p className="text-xs text-muted-foreground">
                      {ref.date} &middot; Score: {ref.relevance_score}
                    </p>
                  </div>
                  <a
                    href={ref.source_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="shrink-0"
                  >
                    <ExternalLink className="h-4 w-4 text-muted-foreground" />
                  </a>
                </div>
              </Card>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
