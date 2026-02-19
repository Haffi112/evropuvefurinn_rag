import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AuthProvider } from "@/hooks/use-auth";
import ProtectedRoute from "@/components/ProtectedRoute";
import AppLayout from "@/components/AppLayout";
import LoginPage from "@/pages/LoginPage";
import DashboardPage from "@/pages/DashboardPage";
import ArticlesPage from "@/pages/ArticlesPage";
import ArticleDetailPage from "@/pages/ArticleDetailPage";
import ArticleFormPage from "@/pages/ArticleFormPage";
import QueryLogPage from "@/pages/QueryLogPage";
import PlaygroundPage from "@/pages/PlaygroundPage";
import SystemHealthPage from "@/pages/SystemHealthPage";
import ReviewersPage from "@/pages/ReviewersPage";
import ReviewsPage from "@/pages/ReviewsPage";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      staleTime: 30_000,
    },
  },
});

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <BrowserRouter basename="/admin">
          <Routes>
            <Route path="/login" element={<LoginPage />} />
            <Route element={<ProtectedRoute />}>
              <Route element={<AppLayout />}>
                <Route path="/dashboard" element={<DashboardPage />} />
                <Route path="/articles" element={<ArticlesPage />} />
                <Route path="/articles/new" element={<ArticleFormPage />} />
                <Route path="/articles/:id" element={<ArticleDetailPage />} />
                <Route path="/articles/:id/edit" element={<ArticleFormPage />} />
                <Route path="/query-log" element={<QueryLogPage />} />
                <Route path="/playground" element={<PlaygroundPage />} />
                <Route path="/system" element={<SystemHealthPage />} />
                <Route path="/reviewers" element={<ReviewersPage />} />
                <Route path="/reviews" element={<ReviewsPage />} />
              </Route>
            </Route>
            <Route path="*" element={<Navigate to="/dashboard" replace />} />
          </Routes>
        </BrowserRouter>
      </AuthProvider>
    </QueryClientProvider>
  );
}
