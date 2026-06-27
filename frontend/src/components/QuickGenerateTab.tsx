import { useEffect, useMemo, useState } from "react";
import {
  getGenerationOptions, generateImageUpload,
  type GenOptions, type ImageCaps,
} from "../api";
import RefineButton from "./RefineButton";
import { useImageLightbox } from "./Lightbox";

const RES_LABEL: Record<string, string> = {
  "512px": "0.5K", "1K": "1K", "2K": "2K · web", "4K": "4K · print",
};
const MIME_LABEL: Record<string, string> = {
  "image/png": "PNG · lossless", "image/jpeg": "JPEG · smaller",
};
const MAX_FILES = 14;

type Variation = { b64: string; mime: string; promptUsed: string; mode: "fresh" | "refine" };

const extOf = (mime: string) => (mime === "image/jpeg" ? "jpg" : "png");

export default function QuickGenerateTab() {
  const [opts, setOpts] = useState<GenOptions | null>(null);
  const [files, setFiles] = useState<File[]>([]);
  const [prompt, setPrompt] = useState("");
  const [model, setModel] = useState("");
  const [aspect, setAspect] = useState("");
  const [imageSize, setImageSize] = useState("");
  const [mimeType, setMimeType] = useState("image/png");
  const [quality, setQuality] = useState(90);
  const [personGen, setPersonGen] = useState("");
  const [thinking, setThinking] = useState("");
  const [variations, setVariations] = useState<Variation[]>([]);
  const [activeIdx, setActiveIdx] = useState(0);
  const [feedback, setFeedback] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ kind: "info" | "error"; text: string } | null>(null);
  const lightbox = useImageLightbox();

  const active = variations[activeIdx] ?? null;
  const caps: ImageCaps | null = useMemo(
    () => (opts && model ? opts.image_caps[model] ?? null : null),
    [opts, model],
  );

  // Object-URL previews for uploaded files; revoke on change/unmount.
  const previews = useMemo(() => files.map((f) => URL.createObjectURL(f)), [files]);
  useEffect(() => () => previews.forEach((u) => URL.revokeObjectURL(u)), [previews]);

  useEffect(() => {
    getGenerationOptions().then((o) => {
      setOpts(o);
      setModel(o.defaults.model);
      setAspect(o.defaults.aspect_ratio);
      setImageSize(o.defaults.resolution);
      setQuality(o.image_compression.default);
    }).catch((e: Error) => setMsg({ kind: "error", text: e.message }));
  }, []);

  // Clamp selections to the chosen model's capabilities whenever it changes.
  useEffect(() => {
    if (!caps) return;
    if (!caps.aspect_ratios.includes(aspect)) setAspect(caps.aspect_ratios[0]);
    if (!caps.image_sizes.includes(imageSize)) setImageSize(caps.image_sizes[0]);
    if (!caps.mime_types.includes(mimeType)) setMimeType(caps.mime_types[0]);
    if (personGen && !caps.person_generation.includes(personGen)) setPersonGen("");
    if (thinking && !caps.thinking_levels.includes(thinking)) setThinking("");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [caps]);

  const addFiles = (e: React.ChangeEvent<HTMLInputElement>) => {
    const picked = Array.from(e.target.files ?? []);
    setFiles((prev) => {
      const next = [...prev, ...picked].slice(0, MAX_FILES);
      if (prev.length + picked.length > MAX_FILES)
        setMsg({ kind: "info", text: `Limited to ${MAX_FILES} reference images.` });
      return next;
    });
    e.target.value = "";
  };
  const removeFile = (i: number) => setFiles((prev) => prev.filter((_, idx) => idx !== i));

  const pushVariation = (b64: string, mime: string, promptUsed: string, mode: "fresh" | "refine") => {
    setVariations((prev) => {
      const next = [...prev, { b64, mime, promptUsed, mode }];
      setActiveIdx(next.length - 1);
      return next;
    });
    setFeedback("");
  };

  const composePrompt = () =>
    feedback.trim() ? `${prompt}\n\nRevision note: ${feedback.trim()}` : prompt;

  const generate = (refine: boolean) => {
    if (refine && !active) return;
    setBusy(true);
    setMsg(null);
    const promptUsed = composePrompt();
    generateImageUpload(files, {
      prompt: promptUsed,
      model: model || undefined,
      resolution: imageSize || undefined,
      aspect_ratio: aspect || undefined,
      mime_type: mimeType || undefined,
      compression_quality: mimeType === "image/jpeg" ? quality : undefined,
      person_generation: personGen || undefined,
      thinking_level: thinking || undefined,
      refine_image_b64: refine && active ? active.b64 : undefined,
    })
      .then((r) => {
        setMsg({ kind: "info", text: r.detail });
        pushVariation(r.image_b64, r.mime_type, promptUsed, refine ? "refine" : "fresh");
      })
      .catch((e: Error) => setMsg({ kind: "error", text: e.message.replace(/^\d+:\s*/, "") }))
      .finally(() => setBusy(false));
  };

  const download = () => {
    if (!active) return;
    const a = document.createElement("a");
    a.href = `data:${active.mime};base64,${active.b64}`;
    a.download = `mockup_${aspect.replace(":", "x")}.${extOf(active.mime)}`;
    a.click();
  };

  const canGenerate = files.length > 0 && prompt.trim().length > 0 && !busy;

  return (
    <div className="stack">
      <section>
        <h2 className="font-display tracking-tight">Quick Generate</h2>
        <p className="text-subtle text-sm">
          Upload reference images and generate a mockup — nothing is saved to the catalog.
        </p>
      </section>

      {/* Upload */}
      <section className="mt-4">
        <p className="section-label mt-0!">Reference images</p>
        <input type="file" accept="image/*" multiple onChange={addFiles} aria-label="Upload reference images" />
        {files.length > 0 && (
          <div className="mt-3 flex flex-wrap gap-2">
            {previews.map((src, i) => (
              <div key={i} className="relative h-20 w-20 overflow-hidden rounded-md border border-line">
                <img src={src} alt={files[i].name} className="h-full w-full object-cover" />
                <button
                  type="button"
                  onClick={() => removeFile(i)}
                  aria-label={`Remove ${files[i].name}`}
                  className="absolute right-0.5 top-0.5 rounded-full bg-black/60 px-1.5 text-xs text-white"
                >
                  ×
                </button>
              </div>
            ))}
          </div>
        )}
      </section>

      {/* Prompt */}
      <section className="mt-5">
        <div className="field">
          <div className="flex items-center justify-between gap-3">
            <label htmlFor="qg-prompt" className="text-xs font-semibold text-subtle">Prompt</label>
            <RefineButton
              kind="image"
              instruction={prompt}
              onRefined={setPrompt}
              onError={(m) => setMsg({ kind: "error", text: m })}
            />
          </div>
          <textarea id="qg-prompt" value={prompt} onChange={(e) => setPrompt(e.target.value)} rows={6} />
        </div>
      </section>

      {/* Options (model-gated) */}
      {opts && caps && (
        <section className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-3">
          <label className="field mb-0!">
            <span className="text-xs font-semibold text-subtle">Model</span>
            <select aria-label="Model" value={model} onChange={(e) => setModel(e.target.value)}>
              {opts.models.map((m) => <option key={m} value={m}>{m}</option>)}
            </select>
          </label>
          <label className="field mb-0!">
            <span className="text-xs font-semibold text-subtle">Aspect ratio</span>
            <select aria-label="Aspect ratio" value={aspect} onChange={(e) => setAspect(e.target.value)}>
              {caps.aspect_ratios.map((a) => <option key={a} value={a}>{a}</option>)}
            </select>
          </label>
          <label className="field mb-0!">
            <span className="text-xs font-semibold text-subtle">Image size</span>
            <select aria-label="Image size" value={imageSize} onChange={(e) => setImageSize(e.target.value)}>
              {caps.image_sizes.map((s) => <option key={s} value={s}>{RES_LABEL[s] ?? s}</option>)}
            </select>
          </label>
          <label className="field mb-0!">
            <span className="text-xs font-semibold text-subtle">Output format</span>
            <select aria-label="Output format" value={mimeType} onChange={(e) => setMimeType(e.target.value)}>
              {caps.mime_types.map((m) => <option key={m} value={m}>{MIME_LABEL[m] ?? m}</option>)}
            </select>
          </label>
          {mimeType === "image/jpeg" && (
            <label className="field mb-0!">
              <span className="text-xs font-semibold text-subtle">JPEG quality · {quality}</span>
              <input
                type="range" aria-label="JPEG quality"
                min={opts.image_compression.min} max={opts.image_compression.max}
                value={quality} onChange={(e) => setQuality(Number(e.target.value))}
              />
            </label>
          )}
          {caps.person_generation.length > 0 && (
            <label className="field mb-0!">
              <span className="text-xs font-semibold text-subtle">People</span>
              <select aria-label="Person generation" value={personGen} onChange={(e) => setPersonGen(e.target.value)}>
                <option value="">— model default —</option>
                {caps.person_generation.map((p) => <option key={p} value={p}>{p}</option>)}
              </select>
            </label>
          )}
          {caps.thinking_levels.length > 0 && (
            <label className="field mb-0!">
              <span className="text-xs font-semibold text-subtle">Thinking</span>
              <select aria-label="Thinking level" value={thinking} onChange={(e) => setThinking(e.target.value)}>
                <option value="">— default —</option>
                {caps.thinking_levels.map((t) => <option key={t} value={t}>{t}</option>)}
              </select>
            </label>
          )}
        </section>
      )}

      <button
        className="btn-primary mt-4 w-full text-[15px] shadow-card"
        style={{ minHeight: 52 }}
        onClick={() => generate(false)}
        disabled={!canGenerate}
      >
        {busy && <span className="spinner" aria-hidden />}
        {busy ? "Generating…" : "Generate Image"}
      </button>
      {files.length === 0 && (
        <p className="mt-2 text-xs text-subtle">Upload at least one reference image to generate.</p>
      )}

      {msg && (
        <p
          className={`mt-4 ${msg.kind === "error" ? "alert alert-error" : "alert alert-info"}`}
          role={msg.kind === "error" ? "alert" : "status"}
          aria-live="polite"
        >
          {msg.text}
        </p>
      )}

      {/* Review & iterate */}
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

          <button
            type="button"
            className="img-zoom mt-2 block w-full overflow-hidden rounded-md! border! border-line! p-0!"
            onClick={() => lightbox.show(`data:${active.mime};base64,${active.b64}`, "Generated mockup")}
            aria-label="Enlarge generated mockup"
          >
            <img src={`data:${active.mime};base64,${active.b64}`} alt="Generated mockup" className="w-full" />
          </button>

          {variations.length > 1 && (
            <div className="mt-2 flex gap-2 overflow-x-auto">
              {variations.map((v, i) => (
                <button
                  key={i}
                  type="button"
                  onClick={() => setActiveIdx(i)}
                  aria-label={`View variation ${i + 1}`}
                  aria-current={i === activeIdx}
                  className={`h-16 w-16 shrink-0 overflow-hidden rounded-md border p-0 ${i === activeIdx ? "border-accent" : "border-line"}`}
                >
                  <img src={`data:${v.mime};base64,${v.b64}`} alt={`Variation ${i + 1}`} className="h-full w-full object-cover" />
                </button>
              ))}
            </div>
          )}

          <div className="field mt-3">
            <div className="flex items-center justify-between gap-3">
              <label htmlFor="qg-feedback" className="text-xs font-semibold text-subtle">
                Feedback (folds into the prompt on refine)
              </label>
              <RefineButton
                kind="image"
                instruction={feedback}
                onRefined={setFeedback}
                onError={(m) => setMsg({ kind: "error", text: m })}
              />
            </div>
            <textarea
              id="qg-feedback"
              value={feedback}
              onChange={(e) => setFeedback(e.target.value)}
              rows={3}
              placeholder="e.g. longer sleeves, warmer background"
            />
          </div>

          <div className="mt-3 flex flex-wrap gap-2">
            <button className="btn-primary" onClick={() => generate(true)} disabled={busy}>
              {busy && <span className="spinner" aria-hidden />} Refine
            </button>
            <button onClick={() => generate(false)} disabled={busy}>Try again</button>
            <button onClick={download} disabled={busy}>Download</button>
          </div>
        </section>
      )}

      {lightbox.node}
    </div>
  );
}
