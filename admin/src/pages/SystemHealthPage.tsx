import { useQuery } from "@tanstack/react-query";
import { apiFetch } from "@/lib/api";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Skeleton } from "@/components/ui/skeleton";
import HealthTab from "@/components/settings/HealthTab";
import ModelsTab from "@/components/settings/ModelsTab";
import PromptsTab from "@/components/settings/PromptsTab";
import type { AppSetting } from "@/components/settings/types";

interface SettingsResponse {
  settings: AppSetting[];
}

export default function SystemHealthPage() {
  const settingsQuery = useQuery<SettingsResponse>({
    queryKey: ["app-settings"],
    queryFn: () => apiFetch("/api/v1/admin/settings"),
  });

  const settings = settingsQuery.data?.settings ?? [];

  return (
    <div className="space-y-6">
      <h1 className="text-3xl font-bold">System</h1>

      <Tabs defaultValue="health">
        <TabsList>
          <TabsTrigger value="health">Health</TabsTrigger>
          <TabsTrigger value="models">Models</TabsTrigger>
          <TabsTrigger value="prompts">Prompts</TabsTrigger>
        </TabsList>

        <TabsContent value="health">
          <HealthTab />
        </TabsContent>

        <TabsContent value="models">
          {settingsQuery.isLoading ? (
            <Skeleton className="h-64 w-full" />
          ) : (
            <ModelsTab settings={settings} />
          )}
        </TabsContent>

        <TabsContent value="prompts">
          {settingsQuery.isLoading ? (
            <Skeleton className="h-64 w-full" />
          ) : (
            <PromptsTab settings={settings} />
          )}
        </TabsContent>
      </Tabs>
    </div>
  );
}
