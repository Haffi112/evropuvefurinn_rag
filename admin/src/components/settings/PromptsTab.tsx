import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import type { AppSetting } from "./types";

interface PromptsTabProps {
  settings: AppSetting[];
}

function PromptCard({ setting }: { setting: AppSetting }) {
  const queryClient = useQueryClient();
  const [value, setValue] = useState(setting.value);
  const [saved, setSaved] = useState(false);

  const hasChanges = value !== setting.value;

  const saveMutation = useMutation({
    mutationFn: () =>
      apiFetch(`/api/v1/admin/settings/${setting.key}`, {
        method: "PUT",
        body: JSON.stringify({ value }),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["app-settings"] });
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    },
  });

  const resetMutation = useMutation({
    mutationFn: () =>
      apiFetch(`/api/v1/admin/settings/${setting.key}`, { method: "DELETE" }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["app-settings"] });
      setValue(setting.default);
    },
  });

  const InputComponent = setting.input_type === "textarea" ? Textarea : Input;

  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="flex items-center gap-2 text-sm">
          {setting.label}
          {setting.is_overridden && (
            <Badge variant="secondary" className="text-xs">
              Customized
            </Badge>
          )}
        </CardTitle>
        <p className="text-xs text-muted-foreground">{setting.description}</p>
      </CardHeader>
      <CardContent className="space-y-3">
        <InputComponent
          value={value}
          onChange={(e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) =>
            setValue(e.target.value)
          }
          rows={setting.input_type === "textarea" ? 6 : undefined}
          className={setting.input_type === "textarea" ? "font-mono text-xs" : ""}
        />
        <div className="flex items-center gap-2">
          <Button
            size="sm"
            onClick={() => saveMutation.mutate()}
            disabled={!hasChanges || saveMutation.isPending}
          >
            {saveMutation.isPending ? "Saving..." : "Save"}
          </Button>
          {setting.is_overridden && (
            <Button
              variant="ghost"
              size="sm"
              onClick={() => resetMutation.mutate()}
              disabled={resetMutation.isPending}
            >
              Reset to Default
            </Button>
          )}
          {saved && <span className="text-xs text-green-600">Saved</span>}
          {saveMutation.isError && (
            <span className="text-xs text-destructive">
              Error: {(saveMutation.error as Error).message}
            </span>
          )}
        </div>
      </CardContent>
    </Card>
  );
}

export default function PromptsTab({ settings }: PromptsTabProps) {
  const promptSettings = settings.filter((s) => s.category === "prompt");

  return (
    <div className="space-y-4">
      {promptSettings.map((s) => (
        <PromptCard key={s.key} setting={s} />
      ))}
    </div>
  );
}
