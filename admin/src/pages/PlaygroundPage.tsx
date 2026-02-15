import { useCallback, useRef, useState } from "react";
import { ExternalLink, Loader2, Send } from "lucide-react";
import { getApiKey } from "@/lib/api";
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
  id: string;
  title: string;
  source_url: string;
  date: string;
  relevance_score: number;
}

export default function PlaygroundPage() {
  const [query, setQuery] = useState("");
  const [language, setLanguage] = useState("auto");
  const [streaming, setStreaming] = useState(true);
  const [answer, setAnswer] = useState("");
  const [refs, setRefs] = useState<Reference[]>([]);
  const [meta, setMeta] = useState<Record<string, unknown>>({});
  const [loading, setLoading] = useState(false);
  const [status, setStatus] = useState("");
  const abortRef = useRef<AbortController | null>(null);

  const handleSubmit = useCallback(async () => {
    if (!query.trim() || loading) return;
    setAnswer("");
    setRefs([]);
    setMeta({});
    setStatus("");
    setLoading(true);

    const controller = new AbortController();
    abortRef.current = controller;

    try {
      if (streaming) {
        // SSE streaming
        const res = await fetch("/api/v1/query", {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-API-Key": getApiKey() ?? "",
          },
          body: JSON.stringify({ query, stream: true, language }),
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
        // JSON mode
        const res = await fetch("/api/v1/query", {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-API-Key": getApiKey() ?? "",
          },
          body: JSON.stringify({ query, stream: false, language }),
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
  }, [query, language, streaming, loading]);

  function handleSSEEvent(event: string, data: Record<string, unknown>) {
    switch (event) {
      case "status":
        setStatus(data.message as string);
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
      <h1 className="text-2xl font-semibold">Query Playground</h1>

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
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={streaming}
                onChange={(e) => setStreaming(e.target.checked)}
                className="rounded"
              />
              Stream
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
        </CardContent>
      </Card>

      {/* Status */}
      {(loading || status) && (
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          {loading && <Loader2 className="h-4 w-4 animate-spin" />}
          <span>{status || "Processing..."}</span>
        </div>
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
            </CardTitle>
          </CardHeader>
          <CardContent>
            <p className="whitespace-pre-wrap">{answer}</p>
          </CardContent>
        </Card>
      )}

      {/* References */}
      {refs.length > 0 && (
        <div className="space-y-2">
          <h3 className="text-sm font-medium text-muted-foreground">
            References ({refs.length})
          </h3>
          <div className="grid gap-2 sm:grid-cols-2">
            {refs.map((ref) => (
              <Card key={ref.id} className="p-3">
                <div className="flex items-start justify-between gap-2">
                  <div>
                    <p className="text-sm font-medium">{ref.title}</p>
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
