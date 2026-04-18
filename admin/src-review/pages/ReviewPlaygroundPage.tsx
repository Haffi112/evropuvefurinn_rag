import { getToken } from "@review/lib/review-api";
import PlaygroundForm from "@/components/PlaygroundForm";

export default function ReviewPlaygroundPage() {
  return (
    <div className="space-y-6">
      <h1 className="text-3xl font-bold">Playground</h1>
      <PlaygroundForm
        endpoint="/api/v1/review/playground"
        getAuthHeaders={() => ({ Authorization: `Bearer ${getToken() ?? ""}` })}
        hint="Queries submitted here are logged with your name and added to the review queue."
      />
    </div>
  );
}
