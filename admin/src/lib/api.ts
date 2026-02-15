const API_KEY_STORAGE = "evropuvefur_api_key";

export function getApiKey(): string | null {
  return localStorage.getItem(API_KEY_STORAGE);
}

export function setApiKey(key: string): void {
  localStorage.setItem(API_KEY_STORAGE, key);
}

export function clearApiKey(): void {
  localStorage.removeItem(API_KEY_STORAGE);
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

export async function apiFetch<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const key = getApiKey();
  const headers: Record<string, string> = {
    ...(options.headers as Record<string, string>),
  };

  if (key) {
    headers["X-API-Key"] = key;
  }
  if (options.body && typeof options.body === "string") {
    headers["Content-Type"] = "application/json";
  }

  const res = await fetch(path, { ...options, headers });

  if (res.status === 401) {
    clearApiKey();
    window.location.href = "/admin/login";
    throw new ApiError(401, "Unauthorized");
  }

  if (!res.ok) {
    const text = await res.text();
    throw new ApiError(res.status, text);
  }

  return res.json() as Promise<T>;
}
