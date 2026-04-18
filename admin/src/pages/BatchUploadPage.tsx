import { useCallback, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import {
  AlertCircle,
  ArrowLeft,
  ChevronDown,
  FileJson,
  Loader2,
  Upload,
  X,
} from "lucide-react";
import { getApiKey } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { cn } from "@/lib/utils";

interface Preview {
  valid: number;
  skipped: Array<{ line: number; reason: string }>;
}

interface CreateResponse {
  id: number;
  total: number;
  skipped: number;
  skipped_reasons: string[];
}

const SAMPLE = `{"id":"q_00001","question_is":"Hvaða reglur gilda um fjórfrelsi á EES-svæðinu?"}
{"id":"q_00002","question_is":"Hver er munurinn á ESB og EES?"}`;

function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

export default function BatchUploadPage() {
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [dragOver, setDragOver] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const navigate = useNavigate();

  const processFile = useCallback(async (f: File) => {
    setError(null);
    setFile(f);
    const text = await f.text();
    const lines = text.split("\n");
    let valid = 0;
    const skipped: Preview["skipped"] = [];
    lines.forEach((raw, i) => {
      const line = raw.trim();
      if (!line) return;
      try {
        const obj = JSON.parse(line);
        if (
          typeof obj.id === "string" &&
          typeof obj.question_is === "string" &&
          obj.question_is.trim()
        ) {
          valid++;
        } else {
          skipped.push({ line: i + 1, reason: "missing id or question_is" });
        }
      } catch (err) {
        skipped.push({
          line: i + 1,
          reason: `invalid JSON: ${(err as Error).message}`,
        });
      }
    });
    setPreview({ valid, skipped });
  }, []);

  const handleFileChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const f = e.target.files?.[0];
      if (f) processFile(f);
    },
    [processFile],
  );

  const handleSubmit = useCallback(async () => {
    if (!file || !preview || preview.valid === 0) return;
    setSubmitting(true);
    setError(null);
    try {
      const fd = new FormData();
      fd.append("file", file);
      const key = getApiKey();
      const res = await fetch("/api/v1/admin/batches", {
        method: "POST",
        headers: { Authorization: `Bearer ${key ?? ""}` },
        body: fd,
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}: ${await res.text()}`);
      const data: CreateResponse = await res.json();
      navigate(`/batches/${data.id}`);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSubmitting(false);
    }
  }, [file, preview, navigate]);

  const clearFile = useCallback(() => {
    setFile(null);
    setPreview(null);
    setError(null);
    if (inputRef.current) inputRef.current.value = "";
  }, []);

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      {/* Back link + header */}
      <div>
        <Link
          to="/batches"
          className="inline-flex items-center text-sm text-muted-foreground hover:text-foreground"
        >
          <ArrowLeft className="mr-1 h-4 w-4" />
          Back to batches
        </Link>
      </div>

      <div className="space-y-2">
        <h1 className="text-3xl font-bold">Upload batch</h1>
        <p className="text-sm text-muted-foreground">
          Each question is answered twice — once via RAG and once via web search — using Gemini 3.1 Pro.
          Completed answers appear in the reviewer queue under the <span className="font-mono">batch</span> user.
        </p>
      </div>

      {/* Dropzone or file summary */}
      {!file ? (
        <button
          type="button"
          onClick={() => inputRef.current?.click()}
          onDragEnter={(e) => {
            e.preventDefault();
            setDragOver(true);
          }}
          onDragOver={(e) => {
            e.preventDefault();
            setDragOver(true);
          }}
          onDragLeave={() => setDragOver(false)}
          onDrop={(e) => {
            e.preventDefault();
            setDragOver(false);
            const f = e.dataTransfer.files?.[0];
            if (f) processFile(f);
          }}
          className={cn(
            "flex w-full cursor-pointer flex-col items-center justify-center gap-3 rounded-lg border-2 border-dashed py-16 text-center transition-all",
            dragOver
              ? "border-primary bg-primary/5 scale-[1.01]"
              : "border-muted-foreground/25 hover:border-primary/50 hover:bg-muted/30",
          )}
        >
          <div className="flex h-14 w-14 items-center justify-center rounded-full bg-primary/10">
            <Upload className="h-6 w-6 text-primary" />
          </div>
          <div>
            <p className="text-base font-medium">
              Drop a <span className="font-mono text-primary">.jsonl</span> file here, or click to browse
            </p>
            <p className="mt-1 text-xs text-muted-foreground">
              One JSON object per line · each needs <code className="rounded bg-muted px-1 font-mono">id</code> and{" "}
              <code className="rounded bg-muted px-1 font-mono">question_is</code>
            </p>
          </div>
          <input
            ref={inputRef}
            type="file"
            accept=".jsonl,.json,.ndjson,text/plain"
            onChange={handleFileChange}
            className="hidden"
          />
        </button>
      ) : (
        <Card className="p-5">
          <div className="flex items-start justify-between gap-3">
            <div className="flex min-w-0 items-start gap-3">
              <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-md bg-primary/10">
                <FileJson className="h-5 w-5 text-primary" />
              </div>
              <div className="min-w-0 flex-1">
                <p className="truncate font-medium" title={file.name}>
                  {file.name}
                </p>
                <p className="text-xs text-muted-foreground">
                  {formatBytes(file.size)}
                </p>
              </div>
            </div>
            <Button
              variant="ghost"
              size="sm"
              onClick={clearFile}
              className="h-8 w-8 shrink-0 p-0 text-muted-foreground hover:text-foreground"
              title="Remove file"
            >
              <X className="h-4 w-4" />
            </Button>
          </div>

          {preview && (
            <div className="mt-4 space-y-3 border-t pt-4">
              <div className="flex flex-wrap items-baseline gap-x-6 gap-y-1">
                <div>
                  <span className="text-2xl font-bold tabular-nums">
                    {preview.valid}
                  </span>
                  <span className="ml-2 text-sm text-muted-foreground">
                    valid questions
                  </span>
                </div>
                <div className="text-sm text-muted-foreground">
                  →{" "}
                  <span className="font-medium text-foreground">
                    {preview.valid * 2}
                  </span>{" "}
                  queue items (RAG + web)
                </div>
              </div>

              {preview.skipped.length > 0 && (
                <details className="group rounded-md border bg-muted/30 p-3 text-sm">
                  <summary className="flex cursor-pointer list-none items-center gap-2 text-destructive">
                    <AlertCircle className="h-4 w-4" />
                    <span>
                      Skipping <strong>{preview.skipped.length}</strong> invalid{" "}
                      {preview.skipped.length === 1 ? "line" : "lines"}
                    </span>
                    <ChevronDown className="ml-auto h-4 w-4 transition-transform group-open:rotate-180" />
                  </summary>
                  <ul className="mt-2 space-y-0.5 border-t pt-2 text-xs">
                    {preview.skipped.slice(0, 20).map((s, i) => (
                      <li key={i} className="font-mono">
                        <span className="text-muted-foreground">
                          Line {s.line}:
                        </span>{" "}
                        {s.reason}
                      </li>
                    ))}
                    {preview.skipped.length > 20 && (
                      <li className="text-muted-foreground">
                        …and {preview.skipped.length - 20} more
                      </li>
                    )}
                  </ul>
                </details>
              )}
            </div>
          )}
        </Card>
      )}

      {error && (
        <div className="flex items-start gap-2 rounded-md border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive">
          <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
          <span>{error}</span>
        </div>
      )}

      {/* Action row */}
      <div className="flex items-center justify-end gap-2">
        {file && (
          <Button variant="outline" onClick={clearFile} disabled={submitting}>
            Cancel
          </Button>
        )}
        <Button
          size="lg"
          onClick={handleSubmit}
          disabled={!preview || preview.valid === 0 || submitting}
        >
          {submitting ? (
            <Loader2 className="mr-2 h-4 w-4 animate-spin" />
          ) : (
            <Upload className="mr-2 h-4 w-4" />
          )}
          {submitting ? "Starting…" : "Start batch"}
        </Button>
      </div>

      {/* Format reference (collapsible) */}
      <details className="group rounded-md border bg-muted/20 text-sm">
        <summary className="flex cursor-pointer list-none items-center gap-2 px-4 py-3 font-medium">
          File format reference
          <ChevronDown className="ml-auto h-4 w-4 transition-transform group-open:rotate-180" />
        </summary>
        <div className="border-t px-4 py-3 text-muted-foreground">
          <p className="mb-2">
            Each line must be a JSON object with at least these fields:
          </p>
          <ul className="mb-3 ml-5 list-disc space-y-0.5 text-xs">
            <li>
              <code className="font-mono text-foreground">id</code> — a string
              identifier for the question (e.g.,{" "}
              <code className="font-mono">q_00001</code>)
            </li>
            <li>
              <code className="font-mono text-foreground">question_is</code> —
              the question text in Icelandic
            </li>
          </ul>
          <p className="mb-1 text-xs">Example:</p>
          <pre className="overflow-x-auto rounded bg-background/60 p-3 text-xs text-foreground">
            {SAMPLE}
          </pre>
        </div>
      </details>
    </div>
  );
}
