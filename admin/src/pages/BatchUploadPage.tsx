import { useCallback, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Loader2, Upload } from "lucide-react";
import { getApiKey } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

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

export default function BatchUploadPage() {
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const navigate = useNavigate();

  const handleFileChange = useCallback(async (e: React.ChangeEvent<HTMLInputElement>) => {
    setError(null);
    const f = e.target.files?.[0];
    if (!f) {
      setFile(null);
      setPreview(null);
      return;
    }
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
        if (typeof obj.id === "string" && typeof obj.question_is === "string" && obj.question_is.trim()) {
          valid++;
        } else {
          skipped.push({ line: i + 1, reason: "missing id or question_is" });
        }
      } catch (err) {
        skipped.push({ line: i + 1, reason: `invalid JSON: ${(err as Error).message}` });
      }
    });
    setPreview({ valid, skipped });
  }, []);

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

  return (
    <div className="space-y-6">
      <h1 className="text-3xl font-bold">Upload batch</h1>
      <p className="text-sm text-muted-foreground">
        Upload a <code>.jsonl</code> file. Each line must be a JSON object with <code>id</code> and{" "}
        <code>question_is</code>. Each question is answered twice (RAG + web search) using Gemini 3.1 Pro.
      </p>

      <Card>
        <CardHeader>
          <CardTitle>Select file</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <input
            type="file"
            accept=".jsonl,.json,text/plain"
            onChange={handleFileChange}
            className="block w-full text-sm"
          />
          {preview && (
            <div className="space-y-2 text-sm">
              <p>
                <span className="font-medium">{preview.valid}</span> valid questions{" "}
                <span className="text-muted-foreground">
                  (→ {preview.valid * 2} queue items)
                </span>
              </p>
              {preview.skipped.length > 0 && (
                <details className="text-xs">
                  <summary className="cursor-pointer text-destructive">
                    Skipping {preview.skipped.length} invalid lines
                  </summary>
                  <ul className="mt-1 space-y-0.5 pl-4">
                    {preview.skipped.slice(0, 20).map((s, i) => (
                      <li key={i}>
                        Line {s.line}: {s.reason}
                      </li>
                    ))}
                    {preview.skipped.length > 20 && (
                      <li>…and {preview.skipped.length - 20} more</li>
                    )}
                  </ul>
                </details>
              )}
            </div>
          )}
          {error && <p className="text-sm text-destructive">{error}</p>}
          <Button
            onClick={handleSubmit}
            disabled={!preview || preview.valid === 0 || submitting}
          >
            {submitting ? (
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
            ) : (
              <Upload className="mr-2 h-4 w-4" />
            )}
            Start batch
          </Button>
        </CardContent>
      </Card>
    </div>
  );
}
