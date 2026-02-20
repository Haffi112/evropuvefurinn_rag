import { useState, useEffect } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import type { AppSetting } from "./types";

interface ModelsTabProps {
  settings: AppSetting[];
}

export default function ModelsTab({ settings }: ModelsTabProps) {
  const modelSettings = settings.filter((s) => s.category === "model");
  const queryClient = useQueryClient();

  const [values, setValues] = useState<Record<string, string>>({});

  useEffect(() => {
    const initial: Record<string, string> = {};
    for (const s of modelSettings) {
      initial[s.key] = s.value;
    }
    setValues(initial);
  }, [settings]);

  const saveMutation = useMutation({
    mutationFn: async (entries: { key: string; value: string }[]) => {
      await Promise.all(
        entries.map((e) =>
          apiFetch(`/api/v1/admin/settings/${e.key}`, {
            method: "PUT",
            body: JSON.stringify({ value: e.value }),
          })
        )
      );
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["app-settings"] }),
  });

  const resetMutation = useMutation({
    mutationFn: async (key: string) => {
      await apiFetch(`/api/v1/admin/settings/${key}`, { method: "DELETE" });
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["app-settings"] }),
  });

  const changed = modelSettings.filter((s) => values[s.key] !== s.value);

  function handleSave() {
    if (changed.length === 0) return;
    saveMutation.mutate(changed.map((s) => ({ key: s.key, value: values[s.key] })));
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Model Configuration</CardTitle>
      </CardHeader>
      <CardContent className="space-y-5">
        {modelSettings.map((s) => (
          <div key={s.key} className="space-y-1.5">
            <div className="flex items-center gap-2">
              <label className="text-sm font-medium">{s.label}</label>
              {s.is_overridden && (
                <Badge variant="secondary" className="text-xs">
                  Customized
                </Badge>
              )}
            </div>
            <p className="text-xs text-muted-foreground">{s.description}</p>
            <div className="flex items-center gap-2">
              <Input
                type={s.input_type === "number" ? "number" : "text"}
                step={s.key === "model.temperature" ? "0.1" : undefined}
                value={values[s.key] ?? ""}
                onChange={(e) => setValues((v) => ({ ...v, [s.key]: e.target.value }))}
                className="max-w-md"
              />
              {s.is_overridden && (
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => resetMutation.mutate(s.key)}
                  disabled={resetMutation.isPending}
                >
                  Reset
                </Button>
              )}
            </div>
          </div>
        ))}

        <div className="flex items-center gap-3 pt-2">
          <Button onClick={handleSave} disabled={changed.length === 0 || saveMutation.isPending}>
            {saveMutation.isPending ? "Saving..." : "Save Changes"}
          </Button>
          {saveMutation.isSuccess && (
            <span className="text-sm text-green-600">Saved</span>
          )}
          {saveMutation.isError && (
            <span className="text-sm text-destructive">
              Error: {(saveMutation.error as Error).message}
            </span>
          )}
        </div>
      </CardContent>
    </Card>
  );
}
