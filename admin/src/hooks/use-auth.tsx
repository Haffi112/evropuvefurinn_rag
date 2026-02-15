import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { apiFetch, clearApiKey, getApiKey, setApiKey } from "@/lib/api";

interface AuthContextValue {
  isAuthenticated: boolean;
  login: (key: string) => Promise<void>;
  logout: () => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [isAuthenticated, setIsAuthenticated] = useState(
    () => getApiKey() !== null,
  );

  const login = useCallback(async (key: string) => {
    setApiKey(key);
    try {
      // Validate the key against the stats endpoint (requires auth)
      await apiFetch("/api/v1/stats");
      setIsAuthenticated(true);
    } catch {
      clearApiKey();
      setIsAuthenticated(false);
      throw new Error("Invalid API key");
    }
  }, []);

  const logout = useCallback(() => {
    clearApiKey();
    setIsAuthenticated(false);
  }, []);

  const value = useMemo(
    () => ({ isAuthenticated, login, logout }),
    [isAuthenticated, login, logout],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
