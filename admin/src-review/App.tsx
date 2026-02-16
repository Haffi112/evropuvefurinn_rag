import { BrowserRouter, Navigate, Outlet, Route, Routes } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ReviewAuthProvider, useReviewAuth } from "@review/hooks/use-review-auth";
import ReviewLayout from "@review/components/ReviewLayout";
import ReviewLoginPage from "@review/pages/ReviewLoginPage";
import ReviewListPage from "@review/pages/ReviewListPage";
import ReviewDetailPage from "@review/pages/ReviewDetailPage";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      staleTime: 30_000,
    },
  },
});

function ProtectedRoute() {
  const { isAuthenticated } = useReviewAuth();
  if (!isAuthenticated) return <Navigate to="/login" replace />;
  return <Outlet />;
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <ReviewAuthProvider>
        <BrowserRouter basename="/review">
          <Routes>
            <Route path="/login" element={<ReviewLoginPage />} />
            <Route element={<ProtectedRoute />}>
              <Route element={<ReviewLayout />}>
                <Route path="/queries" element={<ReviewListPage />} />
                <Route path="/queries/:id" element={<ReviewDetailPage />} />
              </Route>
            </Route>
            <Route path="*" element={<Navigate to="/queries" replace />} />
          </Routes>
        </BrowserRouter>
      </ReviewAuthProvider>
    </QueryClientProvider>
  );
}
