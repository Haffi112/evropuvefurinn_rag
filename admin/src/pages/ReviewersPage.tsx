import { useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { formatDistanceToNow } from "date-fns";
import { Plus, RotateCcw, UserX } from "lucide-react";
import { apiFetch } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

interface ReviewUser {
  id: number;
  username: string;
  is_active: boolean;
  created_at: string;
}

export default function ReviewersPage() {
  const queryClient = useQueryClient();
  const [showCreate, setShowCreate] = useState(false);
  const [resetId, setResetId] = useState<number | null>(null);

  const { data: reviewers, isLoading } = useQuery<ReviewUser[]>({
    queryKey: ["reviewers"],
    queryFn: () => apiFetch("/api/v1/admin/reviewers"),
  });

  const deactivate = useMutation({
    mutationFn: (id: number) =>
      apiFetch(`/api/v1/admin/reviewers/${id}`, { method: "DELETE" }),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ["reviewers"] }),
  });

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-3xl font-bold">Reviewers</h1>
        <Button size="sm" onClick={() => setShowCreate(true)}>
          <Plus className="mr-2 h-4 w-4" />
          Add Reviewer
        </Button>
      </div>

      <Card>
        <CardContent className="p-0">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Username</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Created</TableHead>
                <TableHead className="w-48">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {isLoading && (
                <TableRow>
                  <TableCell colSpan={4} className="text-center text-muted-foreground">
                    Loading...
                  </TableCell>
                </TableRow>
              )}
              {reviewers?.map((r) => (
                <TableRow key={r.id}>
                  <TableCell className="font-medium">{r.username}</TableCell>
                  <TableCell>
                    <Badge variant={r.is_active ? "default" : "secondary"}>
                      {r.is_active ? "Active" : "Inactive"}
                    </Badge>
                  </TableCell>
                  <TableCell className="text-sm text-muted-foreground">
                    {formatDistanceToNow(new Date(r.created_at), {
                      addSuffix: true,
                    })}
                  </TableCell>
                  <TableCell>
                    <div className="flex gap-2">
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() => setResetId(r.id)}
                      >
                        <RotateCcw className="mr-1 h-3 w-3" />
                        Reset
                      </Button>
                      {r.is_active && (
                        <Button
                          variant="outline"
                          size="sm"
                          className="text-destructive"
                          onClick={() => deactivate.mutate(r.id)}
                        >
                          <UserX className="mr-1 h-3 w-3" />
                          Deactivate
                        </Button>
                      )}
                    </div>
                  </TableCell>
                </TableRow>
              ))}
              {reviewers?.length === 0 && (
                <TableRow>
                  <TableCell
                    colSpan={4}
                    className="text-center text-muted-foreground"
                  >
                    No reviewers yet
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      {/* Create dialog */}
      <CreateReviewerDialog
        open={showCreate}
        onOpenChange={setShowCreate}
        onCreated={() => {
          setShowCreate(false);
          queryClient.invalidateQueries({ queryKey: ["reviewers"] });
        }}
      />

      {/* Reset password dialog */}
      <ResetPasswordDialog
        reviewerId={resetId}
        onOpenChange={(open) => {
          if (!open) setResetId(null);
        }}
      />
    </div>
  );
}

// ── Create dialog ──────────────────────────────────────────

function CreateReviewerDialog({
  open,
  onOpenChange,
  onCreated,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onCreated: () => void;
}) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");

  const mutation = useMutation({
    mutationFn: () =>
      apiFetch("/api/v1/admin/reviewers", {
        method: "POST",
        body: JSON.stringify({ username, password }),
      }),
    onSuccess: () => {
      setUsername("");
      setPassword("");
      setError("");
      onCreated();
    },
    onError: (err: Error) => setError(err.message),
  });

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError("");
    mutation.mutate();
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Add Reviewer</DialogTitle>
        </DialogHeader>
        <form onSubmit={handleSubmit} className="space-y-4">
          <Input
            placeholder="Username"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            required
          />
          <Input
            type="password"
            placeholder="Password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
          />
          {error && <p className="text-sm text-destructive">{error}</p>}
          <Button type="submit" disabled={mutation.isPending} className="w-full">
            {mutation.isPending ? "Creating..." : "Create Reviewer"}
          </Button>
        </form>
      </DialogContent>
    </Dialog>
  );
}

// ── Reset password dialog ──────────────────────────────────

function ResetPasswordDialog({
  reviewerId,
  onOpenChange,
}: {
  reviewerId: number | null;
  onOpenChange: (open: boolean) => void;
}) {
  const [password, setPassword] = useState("");
  const [success, setSuccess] = useState(false);

  const mutation = useMutation({
    mutationFn: () =>
      apiFetch(`/api/v1/admin/reviewers/${reviewerId}/reset-password`, {
        method: "PUT",
        body: JSON.stringify({ password }),
      }),
    onSuccess: () => {
      setPassword("");
      setSuccess(true);
      setTimeout(() => {
        setSuccess(false);
        onOpenChange(false);
      }, 1500);
    },
  });

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    mutation.mutate();
  }

  return (
    <Dialog open={reviewerId !== null} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Reset Password</DialogTitle>
        </DialogHeader>
        <form onSubmit={handleSubmit} className="space-y-4">
          <Input
            type="password"
            placeholder="New password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
          />
          {success && (
            <p className="text-sm text-green-600">Password reset.</p>
          )}
          <Button type="submit" disabled={mutation.isPending} className="w-full">
            {mutation.isPending ? "Resetting..." : "Reset Password"}
          </Button>
        </form>
      </DialogContent>
    </Dialog>
  );
}
