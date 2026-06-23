import { supabase } from "./supabaseClient";

// Empty = same origin (single-server deploy, e.g. HF Spaces). For split local
// dev, set VITE_API_URL=http://localhost:8000 in frontend/.env.
// Strip any trailing slash so `${API_URL}${path}` can't produce a double slash
// (e.g. host//api/me), which FastAPI serves a 404 for.
const API_URL = ((import.meta.env.VITE_API_URL as string) ?? "").replace(/\/+$/, "");

/** Friendly fallback when the server gives no `detail` (e.g. an empty body or
 *  a proxy 5xx). Keyed by HTTP status; status 0 means the request never landed. */
const STATUS_HINTS: Record<number, string> = {
  0: "Can't reach the server — check your connection and try again.",
  401: "Your session has expired. Sign in again.",
  403: "You don't have access to this.",
  404: "Not found.",
  409: "That conflicts with the current state.",
  500: "The server hit an unexpected error. Try again shortly.",
  502: "The database request failed. Try again shortly.",
  503: "The server is missing required configuration. Contact the admin.",
};

/** Error from an API call. `status` is 0 for network/CORS failures (no response). */
export class ApiError extends Error {
  status: number;
  detail: string;
  constructor(status: number, detail: string) {
    super(detail || STATUS_HINTS[status] || `Request failed (HTTP ${status}).`);
    this.name = "ApiError";
    this.status = status;
    this.detail = this.message;
  }
}

/** Call the backend with the current Supabase access token attached. */
export async function apiFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const {
    data: { session },
  } = await supabase.auth.getSession();
  const token = session?.access_token;

  let res: Response;
  try {
    res = await fetch(`${API_URL}${path}`, {
      ...init,
      headers: {
        "Content-Type": "application/json",
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...(init.headers ?? {}),
      },
    });
  } catch {
    throw new ApiError(0, STATUS_HINTS[0]);
  }

  if (!res.ok) {
    let detail = "";
    try {
      const body = await res.json();
      if (typeof body?.detail === "string") detail = body.detail;
      else if (body?.detail) detail = JSON.stringify(body.detail);
    } catch {
      /* non-JSON or empty body — fall back to a status hint below */
    }
    throw new ApiError(res.status, detail || STATUS_HINTS[res.status] || res.statusText);
  }
  const text = await res.text();
  return (text ? JSON.parse(text) : undefined) as T;
}

/** Like apiFetch but for multipart/form-data — lets the browser set the boundary. */
export async function apiUpload<T>(path: string, form: FormData): Promise<T> {
  const {
    data: { session },
  } = await supabase.auth.getSession();
  const token = session?.access_token;

  let res: Response;
  try {
    res = await fetch(`${API_URL}${path}`, {
      method: "POST",
      headers: { ...(token ? { Authorization: `Bearer ${token}` } : {}) },
      body: form,
    });
  } catch {
    throw new ApiError(0, STATUS_HINTS[0]);
  }
  if (!res.ok) {
    let detail = "";
    try {
      const body = await res.json();
      if (typeof body?.detail === "string") detail = body.detail;
      else if (body?.detail) detail = JSON.stringify(body.detail);
    } catch {
      /* non-JSON body */
    }
    throw new ApiError(res.status, detail || STATUS_HINTS[res.status] || res.statusText);
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

export interface ProductImageGroup {
  id: string;
  name: string;
  images: ProductImage[];
}

export interface ProductImages {
  loose: ProductImage[];
  groups: ProductImageGroup[];
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
  image_url?: string;
  variation_id?: number;
}

export interface GenPreview {
  status: string;
  detail: string;
  image_b64: string;
}

export interface ApproveResult {
  status: string;
  detail: string;
  image_url: string;
  variation_id?: number;
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
  apiFetch<ProductImages>(`/api/products/${encodeURIComponent(id)}/images`);

export const getProductColors = (id: string) =>
  apiFetch<{ colors: string[] }>(`/api/products/${encodeURIComponent(id)}/colors`);

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

export interface GenOptions {
  models: string[];
  resolutions: string[];
  aspect_ratios: string[];
  defaults: { model: string; resolution: string; aspect_ratio: string };
}

// Static, near-constant; fetched once and shared.
let _genOptionsPromise: Promise<GenOptions> | null = null;
export function getGenerationOptions(): Promise<GenOptions> {
  if (!_genOptionsPromise) {
    _genOptionsPromise = apiFetch<GenOptions>("/api/generate/options").catch((e) => {
      _genOptionsPromise = null;
      throw e;
    });
  }
  return _genOptionsPromise;
}

export const generateImage = (b: {
  productid: string;
  prompt: string;
  image_ids?: string[];
  model?: string;
  resolution?: string;
  aspect_ratio?: string;
  color?: string;
}) =>
  apiFetch<GenPreview>("/api/generate/image", {
    method: "POST",
    body: JSON.stringify(b),
  });

export const approveMockup = (form: FormData) =>
  apiUpload<ApproveResult>("/api/generate/approve", form);

export const generateVideo = (b: { productid: string; prompt: string; image_ids?: string[] }) =>
  apiFetch<GenResult>("/api/generate/video", {
    method: "POST",
    body: JSON.stringify(b),
  });
