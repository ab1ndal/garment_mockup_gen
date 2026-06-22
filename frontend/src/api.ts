import { supabase } from "./supabaseClient";

// Empty = same origin (single-server deploy, e.g. HF Spaces). For split local
// dev, set VITE_API_URL=http://localhost:8000 in frontend/.env.
const API_URL = (import.meta.env.VITE_API_URL as string) ?? "";

/** Call the backend with the current Supabase access token attached. */
export async function apiFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const {
    data: { session },
  } = await supabase.auth.getSession();
  const token = session?.access_token;

  const res = await fetch(`${API_URL}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(init.headers ?? {}),
    },
  });

  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = (await res.json()).detail ?? detail;
    } catch {
      /* ignore */
    }
    throw new Error(`${res.status}: ${detail}`);
  }
  return res.json() as Promise<T>;
}

export interface Me {
  id: string;
  email: string;
  role: string | null;
}

export const getMe = () => apiFetch<Me>("/api/me");
