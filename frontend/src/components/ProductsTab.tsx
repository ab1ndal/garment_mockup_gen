import { useEffect, useState } from "react";
import {
  getCategories, listProducts, listPrompts, listProductImages,
  generateImage, generateVideo,
  type Category, type Product, type Prompt, type ProductImage, type ProductImages,
} from "../api";

export default function ProductsTab() {
  const [cats, setCats] = useState<Category[]>([]);
  const [category, setCategory] = useState("");
  const [idSingle, setIdSingle] = useState("");
  const [idEnd, setIdEnd] = useState("");
  const [pending, setPending] = useState(true);
  const [rows, setRows] = useState<Product[]>([]);
  const [selected, setSelected] = useState<Product | null>(null);
  const [searching, setSearching] = useState(false);
  const [searched, setSearched] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => { getCategories().then(setCats).catch((e) => setErr(e.message)); }, []);

  const search = () => {
    setErr(null);
    setSearching(true);
    const params: Parameters<typeof listProducts>[0] = { pending };
    if (category) params.category = category;
    if (idSingle && idEnd) { params.id_start = idSingle; params.id_end = idEnd; }
    else if (idSingle) params.id = idSingle;
    listProducts(params)
      .then((r) => { setRows(r); setSearched(true); })
      .catch((e) => setErr(e.message))
      .finally(() => setSearching(false));
  };

  // Show the category line per row only when the list spans categories.
  const showRowCategory = category === "";

  return (
    <div className="grid items-start gap-6 lg:grid-cols-[minmax(300px,360px)_1fr]">
      {/* ── Sidebar: find + pick a product (secondary) ── */}
      <aside className="flex flex-col gap-4">
        <form className="card flex flex-col gap-3 p-4" onSubmit={(e) => { e.preventDefault(); search(); }}>
          <div className="field">
            <label htmlFor="flt-cat">Category</label>
            <select id="flt-cat" value={category} onChange={(e) => setCategory(e.target.value)}>
              <option value="">All categories</option>
              {cats.map((c) => <option key={c.categoryid} value={c.categoryid}>{c.name}</option>)}
            </select>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div className="field">
              <label htmlFor="flt-id">Product ID</label>
              <input id="flt-id" placeholder="e.g. BC25001" value={idSingle}
                     onChange={(e) => setIdSingle(e.target.value)} />
            </div>
            <div className="field">
              <label htmlFor="flt-id-end">Range end</label>
              <input id="flt-id-end" placeholder="optional" value={idEnd}
                     onChange={(e) => setIdEnd(e.target.value)} />
            </div>
          </div>
          <div className="flex items-center justify-between gap-3">
            <label className="check min-h-0! text-sm">
              <input type="checkbox" checked={pending}
                     onChange={(e) => setPending(e.target.checked)} />
              Pending only
            </label>
            <button type="submit" className="btn-primary" disabled={searching}>
              {searching && <span className="spinner" aria-hidden />}
              {searching ? "Searching…" : "Search"}
            </button>
          </div>
        </form>

        {err && <p className="alert alert-error" role="alert">{err}</p>}

        {rows.length > 0 ? (
          <ul className="card max-h-[70vh] divide-y divide-line overflow-auto p-0">
            {rows.map((p) => {
              const isSel = selected?.productid === p.productid;
              return (
                <li key={p.productid}>
                  <button
                    type="button"
                    onClick={() => setSelected(p)}
                    aria-current={isSel}
                    className={`flex w-full items-center gap-3 border-0 border-l-2 justify-start! rounded-none! px-4 py-3 text-left min-h-0!
                      ${isSel
                        ? "border-l-accent bg-accent-soft"
                        : "border-l-transparent bg-transparent hover:bg-surface-2"}`}
                  >
                    <span className="min-w-0 flex-1">
                      <span className="block truncate font-medium text-ink">{p.name}</span>
                      <span className="mono block text-xs text-subtle">
                        {p.productid}{showRowCategory && (p.category_name ?? p.categoryid) ? ` · ${p.category_name ?? p.categoryid}` : ""}
                      </span>
                    </span>
                    <span className={p.base_mockup ? "pill pill-done" : "pill pill-pending"}>
                      {p.base_mockup ? "Done" : "Pending"}
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        ) : (
          <p className="empty">
            {searched ? "No products match these filters." : "Run a search to list products."}
          </p>
        )}
      </aside>

      {/* ── Stage: generate the mockup (the main event) ── */}
      {selected
        ? <GenerationStage key={selected.productid} product={selected} />
        : (
          <div className="card flex min-h-[60vh] flex-col items-center justify-center gap-3 p-8 text-center">
            <CanvasIcon />
            <h2 className="font-display text-2xl text-ink">Generate a mockup</h2>
            <p className="max-w-sm text-muted">
              Pick a product from the list to load its source images and start generating.
            </p>
          </div>
        )}
    </div>
  );
}

function GenerationStage({ product }: { product: Product }) {
  const [prompts, setPrompts] = useState<Prompt[]>([]);
  const [promptText, setPromptText] = useState("");
  const [videoPrompt, setVideoPrompt] = useState("");
  const [busy, setBusy] = useState<null | "image" | "video">(null);
  const [msg, setMsg] = useState<{ kind: "info" | "error"; text: string } | null>(null);

  // Source images from Drive: loose (top-level) + per-subfolder variant groups
  const [imgs, setImgs] = useState<ProductImages>({ loose: [], groups: [] });
  const [imgState, setImgState] = useState<"loading" | "ready" | "error">("loading");
  const [imgErr, setImgErr] = useState<string | null>(null);
  const [picked, setPicked] = useState<Set<string>>(new Set());

  useEffect(() => {
    setMsg(null);
    if (!product.categoryid) { setPrompts([]); setPromptText(""); return; }
    listPrompts(product.categoryid).then((ps) => {
      setPrompts(ps);
      const def = ps.find((p) => p.is_default) ?? ps[0];
      setPromptText(def?.body ?? "");
    }).catch((e) => setMsg({ kind: "error", text: e.message }));
  }, [product.productid, product.categoryid]);

  useEffect(() => {
    setImgState("loading"); setImgErr(null);
    setImgs({ loose: [], groups: [] }); setPicked(new Set());
    listProductImages(product.productid)
      .then((r) => { setImgs(r); setImgState("ready"); })
      .catch((e: Error) => { setImgErr(e.message.replace(/^\d+:\s*/, "")); setImgState("error"); });
  }, [product.productid]);

  const togglePick = (id: string) =>
    setPicked((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });

  const run = (kind: "image" | "video") => {
    setBusy(kind);
    setMsg(null);
    const image_ids = [...picked];
    const call = kind === "image"
      ? generateImage({ productid: product.productid, prompt: promptText, image_ids })
      : generateVideo({ productid: product.productid, prompt: videoPrompt, image_ids });
    call
      .then((r) => setMsg({ kind: "info", text: r.detail }))
      .catch((e: Error) => setMsg({ kind: "error", text: e.message.replace(/^\d+:\s*/, "") }))
      .finally(() => setBusy(null));
  };

  const pickedCount = picked.size;
  const totalImages = imgs.loose.length + imgs.groups.reduce((n, g) => n + g.images.length, 0);

  return (
    <div className="card p-6 sm:p-8">
      {/* Header */}
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="font-display text-2xl leading-tight text-ink">{product.name}</h2>
          <p className="mono mt-1 text-sm text-subtle">{product.productid}</p>
        </div>
        {product.producturl
          ? <a href={product.producturl} target="_blank" rel="noreferrer"
               className="text-sm">Open Drive folder ↗</a>
          : <span className="subtle">No Drive folder linked</span>}
      </div>

      {/* Source images */}
      <section className="mt-7">
        <div className="flex items-center justify-between">
          <p className="section-label mt-0!">Source images</p>
          {pickedCount > 0 && (
            <span className="text-xs font-semibold text-accent">{pickedCount} selected</span>
          )}
        </div>
        <p className="mb-3 text-sm text-muted">
          Select one or more images to pass to generation.
        </p>

        {imgState === "loading" && (
          <div className="flex items-center gap-2 text-sm text-subtle">
            <span className="spinner" style={{ color: "var(--accent)" }} aria-hidden /> Loading from Drive…
          </div>
        )}
        {imgState === "error" && <p className="alert alert-error">{imgErr}</p>}
        {imgState === "ready" && totalImages === 0 && (
          <p className="empty py-6!">No images found in this product's Drive folder.</p>
        )}
        {imgState === "ready" && totalImages > 0 && (
          <div className="flex flex-col gap-5">
            {imgs.loose.length > 0 && (
              <ImageGrid images={imgs.loose} picked={picked} onToggle={togglePick} />
            )}
            {imgs.groups.map((g) => (
              <div key={g.id}>
                <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-subtle">
                  {g.name} <span className="text-muted">· {g.images.length}</span>
                </p>
                <ImageGrid images={g.images} picked={picked} onToggle={togglePick} />
              </div>
            ))}
          </div>
        )}
      </section>

      {/* Image prompt + primary CTA — the focal action */}
      <section className="mt-7">
        <div className="field">
          <p className="section-label mt-0!">Image prompt</p>
          <select
            aria-label="Prompt template"
            onChange={(e) => {
              const p = prompts.find((x) => String(x.prompt_id) === e.target.value);
              if (p) setPromptText(p.body);
            }}
          >
            {prompts.length === 0 && <option>No templates for this category</option>}
            {prompts.map((p) => (
              <option key={p.prompt_id} value={p.prompt_id}>
                {p.label}{p.is_default ? " (default)" : ""}
              </option>
            ))}
          </select>
          <textarea
            aria-label="Image prompt text"
            value={promptText}
            onChange={(e) => setPromptText(e.target.value)}
            rows={7}
          />
        </div>
        <button
          className="btn-primary mt-4 w-full text-[15px] shadow-card"
          style={{ minHeight: 52 }}
          onClick={() => run("image")}
          disabled={busy !== null || !promptText.trim()}
        >
          {busy === "image" && <span className="spinner" aria-hidden />}
          {busy === "image"
            ? "Generating…"
            : `Generate Image${pickedCount > 0 ? ` · ${pickedCount} source${pickedCount > 1 ? "s" : ""}` : ""}`}
        </button>
      </section>

      {msg && (
        <p
          className={`mt-4 ${msg.kind === "error" ? "alert alert-error" : "alert alert-info"}`}
          role={msg.kind === "error" ? "alert" : "status"}
          aria-live="polite"
        >
          {msg.text}
        </p>
      )}

      {/* Video — secondary */}
      <section className="mt-7 border-t border-line pt-6">
        <div className="field">
          <p className="section-label mt-0!">Video (custom prompt)</p>
          <textarea
            aria-label="Video prompt text"
            value={videoPrompt}
            onChange={(e) => setVideoPrompt(e.target.value)}
            rows={3}
            placeholder="Describe the video for this product…"
          />
        </div>
        <button
          className="mt-3 w-full"
          onClick={() => run("video")}
          disabled={busy !== null || !videoPrompt.trim()}
        >
          {busy === "video" && <span className="spinner" aria-hidden />}
          {busy === "video" ? "Generating…" : "Generate Video"}
        </button>
      </section>
    </div>
  );
}

function ImageGrid({ images, picked, onToggle }: {
  images: ProductImage[]; picked: Set<string>; onToggle: (id: string) => void;
}) {
  return (
    <div className="grid grid-cols-3 gap-3 sm:grid-cols-4 md:grid-cols-5">
      {images.map((img) => {
        const sel = picked.has(img.id);
        return (
          <button
            key={img.id}
            type="button"
            onClick={() => onToggle(img.id)}
            aria-pressed={sel}
            title={img.name}
            className={`relative aspect-square min-h-0! overflow-hidden rounded-lg! p-0! transition
              ${sel ? "border-accent! ring-2 ring-accent/30" : "border-line! hover:border-line-strong!"}`}
          >
            <img src={img.thumbnail_url} alt={img.name}
                 className="h-full w-full object-cover" loading="lazy" />
            {sel && (
              <span className="absolute right-1.5 top-1.5 grid h-5 w-5 place-items-center rounded-full bg-accent text-[11px] font-bold text-white">
                ✓
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
}

function CanvasIcon() {
  return (
    <svg width="48" height="48" viewBox="0 0 24 24" fill="none"
         stroke="var(--text-subtle)" strokeWidth="1.5" aria-hidden>
      <rect x="3" y="3" width="18" height="18" rx="2" />
      <circle cx="8.5" cy="8.5" r="1.5" />
      <path d="M21 15l-5-5L5 21" />
    </svg>
  );
}
