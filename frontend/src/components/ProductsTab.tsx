import { useEffect, useRef, useState } from "react";
import {
  getCategories, listProducts, listPrompts, listProductImages, getProductColors,
  generateImage, startVideo, getVideoResult, approveMockup, getGenerationOptions,
  type Category, type Product, type Prompt, type ProductImage, type ProductImages,
  type GenOptions,
} from "../api";

// Human labels for the resolution / aspect choices.
const RES_LABEL: Record<string, string> = {
  "1K": "1K", "2K": "2K · web", "4K": "4K · print",
};
const ASPECT_LABEL: Record<string, string> = {
  "1:1": "1:1 · square", "3:4": "3:4 · portrait", "4:3": "4:3 · landscape",
  "9:16": "9:16 · story/reel", "16:9": "16:9 · wide", "2:3": "2:3", "3:2": "3:2", "21:9": "21:9",
};
const VRES_LABEL: Record<string, string> = { "720p": "720p", "1080p": "1080p · 8s only" };

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

type Variation = {
  b64: string;
  promptUsed: string;     // full prompt sent (base + folded feedback)
  feedback: string;       // note that produced this variation ("" for the first)
  mode: "fresh" | "refine";
};

function GenerationStage({ product }: { product: Product }) {
  const [prompts, setPrompts] = useState<Prompt[]>([]);
  const [promptText, setPromptText] = useState("");
  const [videoPrompt, setVideoPrompt] = useState("");
  const [busy, setBusy] = useState<null | "image" | "video">(null);
  const [msg, setMsg] = useState<{ kind: "info" | "error"; text: string } | null>(null);
  const [variations, setVariations] = useState<Variation[]>([]);
  const [activeIdx, setActiveIdx] = useState(0);
  const [feedback, setFeedback] = useState("");
  const active = variations[activeIdx] ?? null;
  const [publishedUrl, setPublishedUrl] = useState<string | null>(null);
  const [publishing, setPublishing] = useState(false);
  const [colors, setColors] = useState<string[]>([]);
  const [color, setColor] = useState("");

  // Generation options (model / quality / aspect) + current selection.
  const [opts, setOpts] = useState<GenOptions | null>(null);
  const [model, setModel] = useState("");
  const [resolution, setResolution] = useState("");
  const [aspect, setAspect] = useState("");
  // Video options + current selection.
  const [vModel, setVModel] = useState("");
  const [vRes, setVRes] = useState("");
  const [vAspect, setVAspect] = useState("");
  const [vDuration, setVDuration] = useState(4);

  useEffect(() => {
    getGenerationOptions().then((o) => {
      setOpts(o);
      setModel(o.defaults.model);
      setResolution(o.defaults.resolution);
      setAspect(o.defaults.aspect_ratio);
      setVModel(o.video_defaults.model);
      setVRes(o.video_defaults.resolution);
      setVAspect(o.video_defaults.aspect_ratio);
      setVDuration(o.video_defaults.duration);
    }).catch(() => {/* options optional; generation still works with server defaults */});
  }, []);

  // VEO: 1080p only renders at 8s — keep the selection valid.
  useEffect(() => {
    if (vRes === "1080p" && vDuration !== 8) setVDuration(8);
  }, [vRes, vDuration]);

  // Stop polling a video job if the user navigates away (component is keyed by
  // product, so it remounts per product).
  const polling = useRef(true);
  useEffect(() => () => { polling.current = false; }, []);

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
    setVariations([]); setActiveIdx(0); setFeedback(""); setPublishedUrl(null);
    listProductImages(product.productid)
      .then((r) => { setImgs(r); setImgState("ready"); })
      .catch((e: Error) => { setImgErr(e.message.replace(/^\d+:\s*/, "")); setImgState("error"); });
  }, [product.productid]);

  useEffect(() => {
    setColor("");
    getProductColors(product.productid)
      .then((r) => setColors(r.colors))
      .catch(() => setColors([]));  // optional; generation works without color
  }, [product.productid]);

  const togglePick = (id: string) =>
    setPicked((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });

  const composePrompt = () =>
    feedback.trim() ? `${promptText}\n\nRevision note: ${feedback.trim()}` : promptText;

  const pushVariation = (b64: string, promptUsed: string, mode: "fresh" | "refine", note: string) => {
    setVariations((prev) => {
      const next = [...prev, { b64, promptUsed, feedback: note, mode }];
      setActiveIdx(next.length - 1);
      return next;
    });
    setFeedback("");
  };

  const run = (kind: "image" | "video") => {
    setBusy(kind);
    setMsg(null);
    const image_ids = [...picked];
    if (kind === "image") {
      setPublishedUrl(null);
      const promptUsed = composePrompt();
      const note = feedback.trim();
      generateImage({
        productid: product.productid, prompt: promptUsed, image_ids,
        color: color || undefined,
        model: model || undefined, resolution: resolution || undefined,
        aspect_ratio: aspect || undefined,
      })
        .then((r) => { setMsg({ kind: "info", text: r.detail }); pushVariation(r.image_b64, promptUsed, "fresh", note); })
        .catch((e: Error) => setMsg({ kind: "error", text: e.message.replace(/^\d+:\s*/, "") }))
        .finally(() => setBusy(null));
    } else {
      const fail = (e: Error) => {
        setMsg({ kind: "error", text: e.message.replace(/^\d+:\s*/, "") });
        setBusy(null);
      };
      const download = (blob: Blob) => {
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = `${product.productid}_${color ? color.replace(/\s+/g, "-") : "mockup"}.mp4`;
        a.click();
        URL.revokeObjectURL(url);
        setMsg({ kind: "info", text: "Video downloaded." });
        setBusy(null);
      };
      const poll = (jobId: string) => {
        if (!polling.current) return;
        getVideoResult(jobId)
          .then((r) => {
            if (!polling.current) return;
            if (r instanceof Blob) return download(r);
            if (r.status === "error") return fail(new Error(r.detail || "Video generation failed."));
            setMsg({ kind: "info", text: "Rendering video… this can take a few minutes." });
            setTimeout(() => poll(jobId), 5000);
          })
          .catch((e: Error) => fail(e));
      };
      startVideo({
        productid: product.productid, prompt: videoPrompt,
        image_url: publishedUrl || undefined,
        color: color || undefined,
        model: vModel || undefined, resolution: vRes || undefined,
        aspect_ratio: vAspect || undefined, duration: vDuration || undefined,
      })
        .then((job) => {
          setMsg({ kind: "info", text: "Rendering video… this can take a few minutes." });
          poll(job.job_id);
        })
        .catch((e: Error) => fail(e));
    }
  };

  const regenerate = (refine: boolean) => {
    if (refine && !active) return;
    setBusy("image");
    setMsg(null);
    setPublishedUrl(null);
    const promptUsed = composePrompt();
    const note = feedback.trim();
    generateImage({
      productid: product.productid, prompt: promptUsed, image_ids: [...picked],
      color: color || undefined,
      model: model || undefined, resolution: resolution || undefined,
      aspect_ratio: aspect || undefined,
      refine_image_b64: refine && active ? active.b64 : undefined,
    })
      .then((r) => { setMsg({ kind: "info", text: r.detail }); pushVariation(r.image_b64, promptUsed, refine ? "refine" : "fresh", note); })
      .catch((e: Error) => setMsg({ kind: "error", text: e.message.replace(/^\d+:\s*/, "") }))
      .finally(() => setBusy(null));
  };

  const publish = (blob: Blob, src: "generated" | "corrected") => {
    setPublishing(true);
    setMsg(null);
    const fd = new FormData();
    fd.append("productid", product.productid);
    if (color) fd.append("color", color);
    if (promptText) fd.append("prompt_text", promptText);
    fd.append("source", src);
    fd.append("image", blob, "mockup.png");
    approveMockup(fd)
      .then((r) => { setPublishedUrl(r.image_url); setMsg({ kind: "info", text: r.detail }); })
      .catch((e: Error) => setMsg({ kind: "error", text: e.message.replace(/^\d+:\s*/, "") }))
      .finally(() => setPublishing(false));
  };

  const approveGenerated = async () => {
    if (!active) return;
    const blob = await (await fetch(`data:image/png;base64,${active.b64}`)).blob();
    publish(blob, "generated");
  };

  const downloadPreview = () => {
    if (!active) return;
    const a = document.createElement("a");
    a.href = `data:image/png;base64,${active.b64}`;
    a.download = `${product.productid}_${color ? color.replace(/\s+/g, "-") : "mockup"}.png`;
    a.click();
  };

  const onCorrectedFile = (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0];
    if (f) publish(f, "corrected");
    e.target.value = "";
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
        {opts && (
          <div className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-3">
            <label className="field mb-0!">
              <span className="text-xs font-semibold text-subtle">Model</span>
              <select aria-label="Model" value={model} onChange={(e) => setModel(e.target.value)}>
                {opts.models.map((m) => <option key={m} value={m}>{m}</option>)}
              </select>
            </label>
            <label className="field mb-0!">
              <span className="text-xs font-semibold text-subtle">Quality</span>
              <select aria-label="Quality" value={resolution} onChange={(e) => setResolution(e.target.value)}>
                {opts.resolutions.map((r) => <option key={r} value={r}>{RES_LABEL[r] ?? r}</option>)}
              </select>
            </label>
            <label className="field mb-0!">
              <span className="text-xs font-semibold text-subtle">Aspect ratio</span>
              <select aria-label="Aspect ratio" value={aspect} onChange={(e) => setAspect(e.target.value)}>
                {opts.aspect_ratios.map((a) => <option key={a} value={a}>{ASPECT_LABEL[a] ?? a}</option>)}
              </select>
            </label>
          </div>
        )}
        {colors.length > 0 && (
          <label className="field mb-0! mt-4">
            <span className="text-xs font-semibold text-subtle">Variant color</span>
            <select aria-label="Variant color" value={color} onChange={(e) => setColor(e.target.value)}>
              <option value="">— no color —</option>
              {colors.map((c) => <option key={c} value={c}>{c}</option>)}
            </select>
          </label>
        )}
        <button
          className="btn-primary mt-4 w-full text-[15px] shadow-card"
          style={{ minHeight: 52 }}
          onClick={() => run("image")}
          disabled={busy !== null || !promptText.trim() || pickedCount === 0}
        >
          {busy === "image" && <span className="spinner" aria-hidden />}
          {busy === "image"
            ? "Generating…"
            : `Generate Image${pickedCount > 0 ? ` · ${pickedCount} source${pickedCount > 1 ? "s" : ""}` : ""}`}
        </button>
        {pickedCount === 0 && (
          <p className="mt-2 text-xs text-subtle">Select at least one source image to generate.</p>
        )}
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

      {/* Review & iterate — in-session variation history */}
      {active && (
        <section className="mt-5">
          <div className="flex items-center justify-between">
            <p className="section-label mt-0!">
              Review · <span className="tabular-nums">{activeIdx + 1} of {variations.length}</span>
            </p>
            <span className={`pill ${active.mode === "refine" ? "pill-done" : "pill-pending"}`}>
              {active.mode === "refine" ? "refined" : "fresh"}
            </span>
          </div>

          {/* Side-by-side: picked sources vs the active variation */}
          <div className="mt-2 grid gap-4 sm:grid-cols-[160px_1fr]">
            <div className="flex flex-row gap-2 overflow-x-auto sm:flex-col">
              {[...imgs.loose, ...imgs.groups.flatMap((g) => g.images)]
                .filter((im) => picked.has(im.id))
                .map((im) => (
                  <img key={im.id} src={im.thumbnail_url} alt={`Source ${im.name}`}
                       className="h-16 w-16 shrink-0 rounded-md border border-line object-cover sm:h-auto sm:w-full" />
                ))}
            </div>
            <img
              src={`data:image/png;base64,${active.b64}`}
              alt={`Variation ${activeIdx + 1}`}
              className="max-w-full rounded-lg border border-line"
            />
          </div>

          {/* History filmstrip */}
          {variations.length > 1 && (
            <div className="mt-3 flex gap-2 overflow-x-auto pb-1">
              {variations.map((v, i) => (
                <button
                  key={i}
                  type="button"
                  onClick={() => setActiveIdx(i)}
                  aria-pressed={i === activeIdx}
                  aria-label={`View variation ${i + 1}${v.feedback ? ` — note: ${v.feedback}` : ""}`}
                  title={v.feedback || (v.mode === "refine" ? "refined" : "fresh")}
                  className={`relative aspect-square h-16 w-16 shrink-0 overflow-hidden rounded-md! p-0! transition
                    ${i === activeIdx ? "border-accent! ring-2 ring-accent/30" : "border-line! hover:border-line-strong!"}`}
                >
                  <img src={`data:image/png;base64,${v.b64}`} alt={`Variation ${i + 1}`}
                       className="h-full w-full object-cover" />
                  <span className="absolute bottom-0.5 right-0.5 rounded bg-black/60 px-1 text-[10px] font-bold text-white">
                    {i + 1}
                  </span>
                </button>
              ))}
            </div>
          )}

          {/* Feedback + regenerate */}
          <div className="field mt-4">
            <label htmlFor="fb">Feedback for next version</label>
            <textarea id="fb" value={feedback} onChange={(e) => setFeedback(e.target.value)} rows={3}
                      placeholder="e.g. make the sleeves longer, warmer background…" />
            <p className="mt-1 text-xs text-subtle">Leave empty to regenerate unchanged.</p>
          </div>
          <div className="flex flex-wrap gap-2">
            <button className="btn" onClick={() => regenerate(true)}
                    disabled={busy !== null || publishing || !active}>
              {busy === "image" && <span className="spinner" aria-hidden />} Refine this
            </button>
            <button className="btn" onClick={() => regenerate(false)}
                    disabled={busy !== null || publishing || pickedCount === 0}>
              Try again
            </button>
          </div>
          <p className="mt-1 text-xs text-subtle">
            <strong>Refine this</strong> edits the current image; <strong>Try again</strong> regenerates from the source images.
          </p>

          {/* Loading skeleton while a regenerate is in flight */}
          {busy === "image" && (
            <div className="mt-3 aspect-square w-full max-w-md animate-pulse rounded-lg bg-surface-2" aria-hidden />
          )}

          {/* Publish — the single primary action */}
          <div className="mt-5 border-t border-line pt-4">
            <div className="flex flex-wrap items-center gap-2">
              <button className="btn-primary" onClick={approveGenerated} disabled={publishing || busy !== null}>
                {publishing && <span className="spinner" aria-hidden />}
                {publishing ? "Publishing…" : "Approve & publish"}
              </button>
              <button className="btn" onClick={downloadPreview} disabled={publishing || busy !== null}>Download</button>
              <label className="btn cursor-pointer">
                Upload corrected
                <input type="file" accept="image/*" className="hidden" onChange={onCorrectedFile} />
              </label>
              <button className="ml-auto text-sm text-subtle hover:text-ink"
                      onClick={() => { setVariations([]); setActiveIdx(0); setFeedback(""); setMsg(null); }}
                      disabled={publishing || busy !== null}>
                Start over
              </button>
            </div>
            {publishedUrl && (
              <p className="alert alert-info mt-3" role="status">
                Published: <a href={publishedUrl} target="_blank" rel="noreferrer">{publishedUrl}</a>
              </p>
            )}
          </div>
        </section>
      )}

      {/* Video — secondary. Animates the published Supabase mockup; downloads the mp4. */}
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
        {opts && (
          <div className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-4">
            <label className="field mb-0!">
              <span className="text-xs font-semibold text-subtle">Model</span>
              <select aria-label="Video model" value={vModel} onChange={(e) => setVModel(e.target.value)}>
                {opts.video_models.map((m) => <option key={m} value={m}>{m}</option>)}
              </select>
            </label>
            <label className="field mb-0!">
              <span className="text-xs font-semibold text-subtle">Quality</span>
              <select aria-label="Video quality" value={vRes} onChange={(e) => setVRes(e.target.value)}>
                {opts.video_resolutions.map((r) => <option key={r} value={r}>{VRES_LABEL[r] ?? r}</option>)}
              </select>
            </label>
            <label className="field mb-0!">
              <span className="text-xs font-semibold text-subtle">Aspect ratio</span>
              <select aria-label="Video aspect ratio" value={vAspect} onChange={(e) => setVAspect(e.target.value)}>
                {opts.video_aspect_ratios.map((a) => <option key={a} value={a}>{ASPECT_LABEL[a] ?? a}</option>)}
              </select>
            </label>
            <label className="field mb-0!">
              <span className="text-xs font-semibold text-subtle">Duration</span>
              <select
                aria-label="Video duration"
                value={vDuration}
                onChange={(e) => setVDuration(Number(e.target.value))}
              >
                {opts.video_durations
                  .filter((d) => vRes !== "1080p" || d === 8)
                  .map((d) => <option key={d} value={d}>{d}s</option>)}
              </select>
            </label>
          </div>
        )}
        <button
          className="mt-3 w-full"
          onClick={() => run("video")}
          disabled={busy !== null || !videoPrompt.trim() || !(publishedUrl || product.base_mockup)}
        >
          {busy === "video" && <span className="spinner" aria-hidden />}
          {busy === "video" ? "Generating…" : "Generate & Download Video"}
        </button>
        {!(publishedUrl || product.base_mockup) && (
          <p className="mt-2 text-xs text-subtle">
            Approve &amp; publish a mockup first — the video animates the published image.
          </p>
        )}
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
