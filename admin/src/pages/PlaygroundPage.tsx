import PlaygroundForm from "@/components/PlaygroundForm";
import { getApiKey } from "@/lib/api";

export default function PlaygroundPage() {
  return (
    <div className="space-y-6">
      <h1 className="text-3xl font-bold">Playground</h1>
      <PlaygroundForm
        endpoint="/api/v1/admin/playground"
        getAuthHeaders={() => ({ Authorization: `Bearer ${getApiKey() ?? ""}` })}
      />
    </div>
  );
}
