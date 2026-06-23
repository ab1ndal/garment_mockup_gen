import { supabase } from "./supabaseClient";

// Empty = same origin (single-server deploy, e.g. HF Spaces). For split local
// dev, set VITE_API_URL=http://localhost:8000 in frontend/.env.
// Strip any trailing slash so `${API_URL}${path}` can't produce a double slash
// (e.g. host//api/me), which FastAPI serves a 404 for.
const API_URL = ((import.meta.env.VITE_API_URL as string) ?? "").replace(/\/+$/, "");

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
  const text = await res.text();
  return (text ? JSON.parse(text) : undefined) as T;
}

export interface Me {
  id: string;
  email: string;
  role: string | null;
}

export const getMe = () => apiFetch<Me>("/api/me");

export interface Category {
  categoryid: string;
  name: string;
}

export interface Product {
  productid: string;
  name: string;
  categoryid: string | null;
  category_name: string | null;
  base_mockup: boolean;
  producturl: string | null;
}

export interface ProductImage {
  id: string;
  name: string;
  mime_type: string;
  thumbnail_url: string;
}

export interface Prompt {
  prompt_id: number;
  categoryid: string;
  label: string;
  body: string;
  is_default: boolean;
}

export interface GenResult {
  status: string;
  detail: string;
}

// Categories are a small, near-static list hit by every tab on mount.
// Cache the in-flight promise so it's fetched once and shared (no refetch on
// tab switch / remount). Call invalidateCategories() if the list ever changes.
let _categoriesPromise: Promise<Category[]> | null = null;
export function getCategories(): Promise<Category[]> {
  if (!_categoriesPromise) {
    _categoriesPromise = apiFetch<Category[]>("/api/categories").catch((e) => {
      _categoriesPromise = null; // allow retry after a failed load
      throw e;
    });
  }
  return _categoriesPromise;
}
export const invalidateCategories = () => {
  _categoriesPromise = null;
};

export function listProducts(p: {
  category?: string;
  id?: string;
  id_start?: string;
  id_end?: string;
  pending?: boolean;
  limit?: number;
  offset?: number;
}): Promise<Product[]> {
  const q = new URLSearchParams();
  if (p.category) q.set("category", p.category);
  if (p.id) q.set("id", p.id);
  if (p.id_start) q.set("id_start", p.id_start);
  if (p.id_end) q.set("id_end", p.id_end);
  q.set("pending", String(p.pending ?? true));
  if (p.limit != null) q.set("limit", String(p.limit));
  if (p.offset != null) q.set("offset", String(p.offset));
  return apiFetch<Product[]>(`/api/products?${q.toString()}`);
}

export const getProduct = (id: string) =>
  apiFetch<Product>(`/api/products/${encodeURIComponent(id)}`);

export const listProductImages = (id: string) =>
  apiFetch<ProductImage[]>(`/api/products/${encodeURIComponent(id)}/images`);

export const listPrompts = (categoryid: string) =>
  apiFetch<Prompt[]>(`/api/prompts?categoryid=${encodeURIComponent(categoryid)}`);

export const createPrompt = (b: {
  categoryid: string;
  label: string;
  body: string;
  is_default?: boolean;
}) => apiFetch<Prompt>("/api/prompts", { method: "POST", body: JSON.stringify(b) });

export const updatePrompt = (
  id: number,
  b: { label?: string; body?: string; is_default?: boolean }
) =>
  apiFetch<Prompt>(`/api/prompts/${id}`, {
    method: "PATCH",
    body: JSON.stringify(b),
  });

export const deletePrompt = (id: number) =>
  apiFetch<void>(`/api/prompts/${id}`, { method: "DELETE" });

export const generateImage = (b: { productid: string; prompt: string; image_ids?: string[] }) =>
  apiFetch<GenResult>("/api/generate/image", {
    method: "POST",
    body: JSON.stringify(b),
  });

export const generateVideo = (b: { productid: string; prompt: string; image_ids?: string[] }) =>
  apiFetch<GenResult>("/api/generate/video", {
    method: "POST",
    body: JSON.stringify(b),
  });
