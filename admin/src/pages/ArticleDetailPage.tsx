import { Link, useNavigate, useParams } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, ExternalLink, Pencil, Trash2 } from "lucide-react";
import { apiFetch } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Separator } from "@/components/ui/separator";

interface Article {
  id: string;
  title: string;
  question: string;
  answer: string;
  source_url: string;
  date: string;
  author: string;
  categories: string[];
  tags: string[];
  created_at: string;
  updated_at: string | null;
}

export default function ArticleDetailPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const { data: article, isLoading } = useQuery<Article>({
    queryKey: ["article", id],
    queryFn: () => apiFetch(`/api/v1/articles/${id}`),
    enabled: !!id,
  });

  const deleteMutation = useMutation({
    mutationFn: () =>
      apiFetch(`/api/v1/articles/${id}`, { method: "DELETE" }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["articles"] });
      navigate("/articles", { replace: true });
    },
  });

  if (isLoading) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-8 w-64" />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }

  if (!article) {
    return <p className="text-muted-foreground">Article not found.</p>;
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-4">
        <Button variant="ghost" size="sm" asChild>
          <Link to="/articles">
            <ArrowLeft className="mr-2 h-4 w-4" />
            Back
          </Link>
        </Button>
        <div className="flex-1" />
        <Button variant="outline" size="sm" asChild>
          <Link to={`/articles/${id}/edit`}>
            <Pencil className="mr-2 h-4 w-4" />
            Edit
          </Link>
        </Button>
        <Button
          variant="destructive"
          size="sm"
          onClick={() => {
            if (confirm("Delete this article?")) deleteMutation.mutate();
          }}
          disabled={deleteMutation.isPending}
        >
          <Trash2 className="mr-2 h-4 w-4" />
          Delete
        </Button>
      </div>

      <Card className="card-accent">
        <CardHeader>
          <CardTitle>{article.title}</CardTitle>
          <div className="flex flex-wrap gap-2 text-sm text-muted-foreground">
            <span>ID: {article.id}</span>
            <span>Author: {article.author}</span>
            <span>Date: {article.date}</span>
            <a
              href={article.source_url}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1 hover:underline"
            >
              Source <ExternalLink className="h-3 w-3" />
            </a>
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          <div>
            <h3 className="mb-1 text-xs font-bold uppercase tracking-wider text-muted-foreground">
              Question
            </h3>
            <p className="whitespace-pre-wrap">{article.question}</p>
          </div>
          <Separator />
          <div>
            <h3 className="mb-1 text-xs font-bold uppercase tracking-wider text-muted-foreground">
              Answer
            </h3>
            <p className="whitespace-pre-wrap">{article.answer}</p>
          </div>
          <Separator />
          <div className="flex flex-wrap gap-2">
            {article.categories.map((c) => (
              <Badge key={c} variant="secondary">
                {c}
              </Badge>
            ))}
            {article.tags.map((t) => (
              <Badge key={t} variant="outline">
                {t}
              </Badge>
            ))}
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
