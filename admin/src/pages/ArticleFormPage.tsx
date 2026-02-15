import { useEffect, useState, type FormEvent } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

interface ArticleData {
  id: string;
  title: string;
  question: string;
  answer: string;
  source_url: string;
  date: string;
  author: string;
  categories: string[];
  tags: string[];
}

const EMPTY: ArticleData = {
  id: "",
  title: "",
  question: "",
  answer: "",
  source_url: "",
  date: "",
  author: "",
  categories: [],
  tags: [],
};

export default function ArticleFormPage() {
  const { id } = useParams<{ id: string }>();
  const isEdit = !!id;
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [form, setForm] = useState<ArticleData>(EMPTY);
  const [categoriesRaw, setCategoriesRaw] = useState("");
  const [tagsRaw, setTagsRaw] = useState("");
  const [error, setError] = useState("");

  const { data: existing } = useQuery<ArticleData>({
    queryKey: ["article", id],
    queryFn: () => apiFetch(`/api/v1/articles/${id}`),
    enabled: isEdit,
  });

  useEffect(() => {
    if (existing) {
      setForm(existing);
      setCategoriesRaw(existing.categories.join(", "));
      setTagsRaw(existing.tags.join(", "));
    }
  }, [existing]);

  const mutation = useMutation({
    mutationFn: (data: ArticleData) => {
      if (isEdit) {
        return apiFetch(`/api/v1/articles/${id}`, {
          method: "PUT",
          body: JSON.stringify(data),
        });
      }
      return apiFetch("/api/v1/articles", {
        method: "POST",
        body: JSON.stringify(data),
      });
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["articles"] });
      queryClient.invalidateQueries({ queryKey: ["article", id] });
      navigate(isEdit ? `/articles/${id}` : "/articles", { replace: true });
    },
    onError: (err) => setError(String(err)),
  });

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    const data: ArticleData = {
      ...form,
      categories: categoriesRaw
        .split(",")
        .map((s) => s.trim())
        .filter(Boolean),
      tags: tagsRaw
        .split(",")
        .map((s) => s.trim())
        .filter(Boolean),
    };
    mutation.mutate(data);
  }

  function field(key: keyof ArticleData, label: string, multiline = false) {
    const Component = multiline ? Textarea : Input;
    return (
      <div className="space-y-1">
        <label className="text-sm font-medium">{label}</label>
        <Component
          value={form[key] as string}
          onChange={(e) => setForm((f) => ({ ...f, [key]: e.target.value }))}
          required
        />
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-2xl space-y-6">
      <h1 className="text-2xl font-semibold">
        {isEdit ? "Edit Article" : "New Article"}
      </h1>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Article Details</CardTitle>
        </CardHeader>
        <CardContent>
          <form onSubmit={handleSubmit} className="space-y-4">
            {field("id", "ID")}
            {field("title", "Title")}
            {field("question", "Question", true)}
            {field("answer", "Answer", true)}
            {field("source_url", "Source URL")}
            {field("date", "Date")}
            {field("author", "Author")}
            <div className="space-y-1">
              <label className="text-sm font-medium">
                Categories (comma separated)
              </label>
              <Input
                value={categoriesRaw}
                onChange={(e) => setCategoriesRaw(e.target.value)}
              />
            </div>
            <div className="space-y-1">
              <label className="text-sm font-medium">
                Tags (comma separated)
              </label>
              <Input
                value={tagsRaw}
                onChange={(e) => setTagsRaw(e.target.value)}
              />
            </div>
            {error && <p className="text-sm text-destructive">{error}</p>}
            <div className="flex gap-2">
              <Button type="submit" disabled={mutation.isPending}>
                {mutation.isPending
                  ? "Saving..."
                  : isEdit
                    ? "Update"
                    : "Create"}
              </Button>
              <Button
                type="button"
                variant="outline"
                onClick={() => navigate(-1)}
              >
                Cancel
              </Button>
            </div>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}
