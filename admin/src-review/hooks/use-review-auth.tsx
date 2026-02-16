import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { reviewFetch, getToken, setToken, clearToken } from "@review/lib/review-api";

interface ReviewAuthContextValue {
  isAuthenticated: boolean;
  username: string | null;
  login: (username: string, password: string) => Promise<void>;
  logout: () => void;
}

const ReviewAuthContext = createContext<ReviewAuthContextValue | null>(null);

export function ReviewAuthProvider({ children }: { children: ReactNode }) {
  const [isAuthenticated, setIsAuthenticated] = useState(() => getToken() !== null);
  const [username, setUsername] = useState<string | null>(() => {
    const token = getToken();
    if (!token) return null;
    try {
      const payload = JSON.parse(atob(token.split(".")[1]));
      return payload.username ?? null;
    } catch {
      return null;
    }
  });

  const login = useCallback(async (user: string, password: string) => {
    const res = await reviewFetch<{ token: string; username: string }>(
      "/api/v1/review/auth/login",
      {
        method: "POST",
        body: JSON.stringify({ username: user, password }),
      },
    );
    setToken(res.token);
    setUsername(res.username);
    setIsAuthenticated(true);
  }, []);

  const logout = useCallback(() => {
    clearToken();
    setUsername(null);
    setIsAuthenticated(false);
  }, []);

  const value = useMemo(
    () => ({ isAuthenticated, username, login, logout }),
    [isAuthenticated, username, login, logout],
  );

  return (
    <ReviewAuthContext.Provider value={value}>
      {children}
    </ReviewAuthContext.Provider>
  );
}

export function useReviewAuth(): ReviewAuthContextValue {
  const ctx = useContext(ReviewAuthContext);
  if (!ctx) throw new Error("useReviewAuth must be used within ReviewAuthProvider");
  return ctx;
}
