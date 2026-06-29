import { useEffect, useMemo, useRef, useState } from "react";
import {
  getGenerationOptions, startVideoUpload, getVideoResult,
  type GenOptions, type VideoCaps, type VideoJob,
} from "../api";
import RefineButton from "./RefineButton";
import DropZone from "./DropZone";
import { DownloadIcon, TrashIcon } from "./icons";

type Mode = "text" | "image" | "frames" | "reference" | "extend";

const MODE_LABELS: Record<Mode, string> = {
  text: "Text → Video",
  image: "Image → Video",
  frames: "First + Last",
  reference: "Reference",
  extend: "Extend +7s",
};
const MODE_HINT: Record<Mode, string> = {
  text: "Generate from the prompt alone — no upload needed.",
  image: "Animate a single start frame.",
  frames: "Interpolate motion between a start and an end frame.",
  reference: "Up to 3 reference images keep the garment and model consistent.",
  extend: "Extend the active clip by 7 seconds.",
};
const MAX_REFS = 3;
// Pre-filled so every render avoids the artifacts we consistently dislike.
const DEFAULT_NEGATIVE =
  "sudden jerky camera movements, skips in frame, missing fabric, unrealistic movements";
// Positive-steer preamble — VEO obeys "do X" far better than negatives. Seeds
// camera stability, lens/scene context, and realistic cloth motion; the editor
// fills the garment + pose details where marked.
const DEFAULT_PROMPT =
  "Single continuous locked-off shot, no cuts. Cinematic full-frame camera on a static tripod, " +
  "35mm lens at eye level. The camera holds steady or moves with a slow, smooth, gentle push-in — " +
  "no sudden pans, whips, or jerky motion. The model moves slowly and naturally, with realistic " +
  "fabric drape, weight, and cloth physics; the garment stays fully intact with no missing or " +
  "morphing fabric. Soft, even studio lighting on a clean seamless background, photorealistic, " +
  "24fps motion blur.\n\n" +
  "Garment & scene: ";
const POLL_MS = 5000;
const MAX_POLLS = 72;

type Clip = { url: string; promptUsed: string; mode: Mode };

export default function QuickVideoTab() {
  const [opts, setOpts] = useState<GenOptions | null>(null);
  const [mode, setMode] = useState<Mode>("image");
  const [model, setModel] = useState("");
  const [aspect, setAspect] = useState("");
  const [resolution, setResolution] = useState("");
  const [duration, setDuration] = useState<number>(4);
  const [personGen, setPersonGen] = useState("");
  const [negative, setNegative] = useState(DEFAULT_NEGATIVE);
  const [prompt, setPrompt] = useState(DEFAULT_PROMPT);

  const [startFrame, setStartFrame] = useState<File | null>(null);
  const [lastFrame, setLastFrame] = useState<File | null>(null);
  const [refImages, setRefImages] = useState<File[]>([]);

  const clipUrlsRef = useRef<string[]>([]);
  const [clips, setClips] = useState<Clip[]>([]);
  const [activeIdx, setActiveIdx] = useState(0);
  const [feedback, setFeedback] = useState("");
  const [busy, setBusy] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [msg, setMsg] = useState<{ kind: "info" | "error"; text: string } | null>(null);

  const active = clips[activeIdx] ?? null;
  const caps: VideoCaps | null = useMemo(
    () => (opts && model ? opts.video_caps[model] ?? null : null),
    [opts, model],
  );

  // Object-URL previews for image uploads; revoke on change/unmount.
  const startUrl = useMemo(() => (startFrame ? URL.createObjectURL(startFrame) : ""), [startFrame]);
  const lastUrl = useMemo(() => (lastFrame ? URL.createObjectURL(lastFrame) : ""), [lastFrame]);
  const refUrls = useMemo(() => refImages.map((f) => URL.createObjectURL(f)), [refImages]);
  useEffect(() => () => { if (startUrl) URL.revokeObjectURL(startUrl); }, [startUrl]);
  useEffect(() => () => { if (lastUrl) URL.revokeObjectURL(lastUrl); }, [lastUrl]);
  useEffect(() => () => refUrls.forEach((u) => URL.revokeObjectURL(u)), [refUrls]);
  // Revoke clip URLs on unmount only.
  useEffect(() => () => clipUrlsRef.current.forEach((u) => URL.revokeObjectURL(u)), []);

  useEffect(() => {
    getGenerationOptions().then((o) => {
      setOpts(o);
      const d = o.video_defaults;
      setModel(d.model);
      setAspect(d.aspect_ratio);
      setResolution(d.resolution);
      setDuration(8); // prefer 8s for smoother motion; clamp effect falls back if unsupported
    }).catch((e: Error) => setMsg({ kind: "error", text: e.message }));
  }, []);

  // If the chosen model can't do the current mode, fall back to "image".
  useEffect(() => {
    if (caps && !caps.modes.includes(mode)) setMode("image");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [caps]);

  // Clamp aspect/resolution/duration to caps; apply VEO cross-field rules.
  useEffect(() => {
    if (!caps) return;
    if (!caps.aspect_ratios.includes(aspect)) setAspect(caps.aspect_ratios[0]);
    let res = caps.resolutions.includes(resolution) ? resolution : caps.resolutions[0];
    if (mode === "extend") res = "720p";                       // extension is 720p only
    if (res !== resolution) setResolution(res);
    const needs8 = mode === "reference" || mode === "frames" || res === "1080p";
    if (needs8 && duration !== 8) setDuration(8);
    else if (!caps.durations.includes(duration)) setDuration(caps.durations[0]);
    if (personGen && !caps.person_generation.includes(personGen)) setPersonGen("");
  }, [caps, mode, resolution, duration]);

  const durationLocked = mode === "reference" || mode === "frames" || resolution === "1080p";
  const resolutionLocked = mode === "extend";

  const composePrompt = () =>
    feedback.trim() ? `${prompt}\n\nRevision note: ${feedback.trim()}` : prompt;

  const inputsReady = () => {
    if (mode === "image") return !!startFrame;
    if (mode === "frames") return !!startFrame && !!lastFrame;
    if (mode === "reference") return refImages.length > 0;
    if (mode === "extend") return !!active;
    return true; // text
  };
  const canGenerate = prompt.trim().length > 0 && inputsReady() && !busy;

  const poll = async (jobId: string): Promise<Blob> =>
    new Promise<Blob>((resolve, reject) => {
      let attempts = 0;
      const tick = async () => {
        try {
          const r = await getVideoResult(jobId);
          if (r instanceof Blob) return resolve(r);
          if ((r as VideoJob).status === "error")
            return reject(new Error((r as VideoJob).detail || "Video generation failed."));
          attempts += 1;
          if (attempts >= MAX_POLLS)
            return reject(new Error("Video generation timed out — try again."));
          setTimeout(tick, POLL_MS);
        } catch (e) {
          reject(e as Error);
        }
      };
      tick();
    });

  const generate = async () => {
    setBusy(true);
    setMsg(null);
    setElapsed(0);
    const timer = setInterval(() => setElapsed((s) => s + 1), 1000);
    const promptUsed = composePrompt();
    try {
      let extendVideo: Blob | undefined;
      if (mode === "extend") {
        if (!active) throw new Error("Generate a clip first, then extend it.");
        extendVideo = await (await fetch(active.url)).blob();
      }
      const { job_id } = await startVideoUpload(
        {
          mode, prompt: promptUsed, model: model || undefined,
          aspect_ratio: aspect || undefined, resolution: resolution || undefined,
          duration, negative_prompt: negative || undefined,
          person_generation: personGen || undefined,
        },
        {
          startFrame: mode === "image" || mode === "frames" ? startFrame ?? undefined : undefined,
          lastFrame: mode === "frames" ? lastFrame ?? undefined : undefined,
          referenceImages: mode === "reference" ? refImages : undefined,
          extendVideo,
        },
      );
      const blob = await poll(job_id);
      const url = URL.createObjectURL(blob);
      clipUrlsRef.current.push(url);
      setClips((prev) => {
        const next = [...prev, { url, promptUsed, mode }];
        setActiveIdx(next.length - 1);
        return next;
      });
      setFeedback("");
      setMsg({ kind: "info", text: "Video ready." });
    } catch (e) {
      setMsg({ kind: "error", text: (e as Error).message.replace(/^\d+:\s*/, "") });
    } finally {
      clearInterval(timer);
      setBusy(false);
    }
  };

  const download = () => {
    if (!active) return;
    const a = document.createElement("a");
    a.href = active.url;
    a.download = `quick_video_${aspect.replace(":", "x")}.mp4`;
    a.click();
  };

  const startExtend = () => { setMode("extend"); setFeedback(""); };

  return (
    <div className="stack">
      <section>
        <h2 className="font-display tracking-tight">Quick Video</h2>
        <p className="text-subtle text-sm">
          Generate a short garment video with VEO — nothing is saved to the catalog.
        </p>
      </section>

      {/* Mode selector */}
      <section className="mt-4">
        <p className="section-label mt-0!">Mode</p>
        <div className="flex flex-wrap gap-2" role="group" aria-label="Generation mode">
          {(Object.keys(MODE_LABELS) as Mode[]).map((m) => {
            const supported = !caps || caps.modes.includes(m);
            const isExtend = m === "extend";
            const disabled = !supported || (isExtend && !active);
            const reason = !supported
              ? "Not available on this model"
              : isExtend && !active
                ? "Generate a clip first"
                : undefined;
            return (
              <button
                key={m}
                type="button"
                className={`pill ${mode === m ? "pill-done" : "pill-pending"}`}
                aria-pressed={mode === m}
                disabled={disabled}
                title={reason}
                onClick={() => setMode(m)}
              >
                {MODE_LABELS[m]}
              </button>
            );
          })}
        </div>
        <p className="mt-2 text-xs text-subtle">{MODE_HINT[mode]}</p>
      </section>

      {/* Mode-specific uploads */}
      {(mode === "image" || mode === "frames") && (
        <section className="mt-4">
          <p className="section-label mt-0!">{mode === "frames" ? "Start frame" : "Source frame"}</p>
          <DropZone
            ariaLabel={mode === "frames" ? "Start frame" : "Source frame"}
            hint="PNG or JPG · one image"
            file={startFrame}
            previewUrl={startUrl}
            onFiles={(f) => setStartFrame(f[0] ?? null)}
            onClear={() => setStartFrame(null)}
          />
        </section>
      )}
      {mode === "frames" && (
        <section className="mt-4">
          <p className="section-label mt-0!">End frame</p>
          <DropZone
            ariaLabel="End frame"
            hint="PNG or JPG · one image"
            file={lastFrame}
            previewUrl={lastUrl}
            onFiles={(f) => setLastFrame(f[0] ?? null)}
            onClear={() => setLastFrame(null)}
          />
        </section>
      )}
      {mode === "reference" && (
        <section className="mt-4">
          <p className="section-label mt-0!">Reference images · up to {MAX_REFS}</p>
          {refImages.length < MAX_REFS && (
            <DropZone
              ariaLabel="Reference images"
              hint={`PNG or JPG · ${MAX_REFS - refImages.length} more`}
              multiple
              onFiles={(picked) => setRefImages((prev) => [...prev, ...picked].slice(0, MAX_REFS))}
            />
          )}
          {refUrls.length > 0 && (
            <div className="mt-2 flex flex-wrap gap-2">
              {refUrls.map((src, i) => (
                <div key={i} className="relative h-20 w-20 overflow-hidden rounded-lg border border-line">
                  <img src={src} alt={`Reference ${i + 1}`} className="h-full w-full object-cover" />
                  <button
                    type="button"
                    onClick={() => setRefImages((prev) => prev.filter((_, idx) => idx !== i))}
                    aria-label={`Remove reference ${i + 1}`}
                    className="absolute right-1 top-1 grid h-6 w-6 place-items-center rounded-full bg-black/60 text-white transition-colors hover:bg-black/80"
                  >
                    <TrashIcon size={13} />
                  </button>
                </div>
              ))}
            </div>
          )}
        </section>
      )}
      {mode === "extend" && (
        <section className="mt-4">
          <p className="section-label mt-0!">Source clip</p>
          <p className="text-xs text-subtle">
            {active ? "Extending the active clip below by 7 seconds." : "Generate a clip first to extend it."}
          </p>
        </section>
      )}

      {/* Prompt */}
      <section className="mt-5">
        <div className="field">
          <div className="flex items-center justify-between gap-3">
            <label htmlFor="qv-prompt" className="text-xs font-semibold text-subtle">Prompt</label>
            <RefineButton
              kind="video"
              instruction={prompt}
              onRefined={setPrompt}
              onError={(m) => setMsg({ kind: "error", text: m })}
            />
          </div>
          <textarea id="qv-prompt" value={prompt} onChange={(e) => setPrompt(e.target.value)} rows={5} />
        </div>
      </section>

      {/* Options (model-gated) */}
      {opts && caps && (
        <section className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-3">
          <label className="field mb-0!">
            <span className="text-xs font-semibold text-subtle">Model</span>
            <select aria-label="Model" value={model} onChange={(e) => setModel(e.target.value)}>
              {opts.video_models.map((m) => <option key={m} value={m}>{m}</option>)}
            </select>
          </label>
          <label className="field mb-0!">
            <span className="text-xs font-semibold text-subtle">Aspect ratio</span>
            <select aria-label="Aspect ratio" value={aspect} onChange={(e) => setAspect(e.target.value)}>
              {caps.aspect_ratios.map((a) => <option key={a} value={a}>{a}</option>)}
            </select>
          </label>
          <label className="field mb-0!">
            <span className="text-xs font-semibold text-subtle">Resolution</span>
            <select
              aria-label="Resolution" value={resolution} disabled={resolutionLocked}
              onChange={(e) => setResolution(e.target.value)}
            >
              {caps.resolutions.map((r) => <option key={r} value={r}>{r}</option>)}
            </select>
            {resolutionLocked && <span className="mt-1 text-xs text-subtle">720p only when extending.</span>}
          </label>
          <label className="field mb-0!">
            <span className="text-xs font-semibold text-subtle">Duration</span>
            <select
              aria-label="Duration" value={duration} disabled={durationLocked}
              onChange={(e) => setDuration(Number(e.target.value))}
            >
              {caps.durations.map((d) => <option key={d} value={d}>{d}s</option>)}
            </select>
            {durationLocked && <span className="mt-1 text-xs text-subtle">8s required for this mode/resolution.</span>}
          </label>
          {caps.person_generation.length > 0 && (
            <label className="field mb-0!">
              <span className="text-xs font-semibold text-subtle">People</span>
              <select aria-label="Person generation" value={personGen} onChange={(e) => setPersonGen(e.target.value)}>
                <option value="">— model default —</option>
                {caps.person_generation.map((p) => <option key={p} value={p}>{p}</option>)}
              </select>
            </label>
          )}
          <label className="field mb-0! sm:col-span-3">
            <span className="text-xs font-semibold text-subtle">Negative prompt (optional)</span>
            <input
              type="text" aria-label="Negative prompt" value={negative}
              onChange={(e) => setNegative(e.target.value)}
              placeholder="e.g. morphing faces, jerky camera"
            />
          </label>
        </section>
      )}

      <button
        className="btn-primary mt-4 w-full text-[15px] shadow-card"
        style={{ minHeight: 52 }}
        onClick={generate}
        disabled={!canGenerate}
      >
        {busy && <span className="spinner" aria-hidden />}
        {busy ? `Rendering… ${elapsed}s` : "Generate Video"}
      </button>
      {busy && (
        <p className="mt-2 text-xs text-subtle" aria-live="polite">
          VEO renders take a minute or two — keep this tab open.
        </p>
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
              Review · <span className="tabular-nums">{activeIdx + 1} of {clips.length}</span>
            </p>
            <span className="pill pill-done">{MODE_LABELS[active.mode]}</span>
          </div>

          <video
            key={active.url}
            src={active.url}
            controls
            className="mt-2 w-full rounded-md border border-line"
          />

          {clips.length > 1 && (
            <div className="mt-2 flex gap-2 overflow-x-auto">
              {clips.map((c, i) => (
                <button
                  key={i}
                  type="button"
                  onClick={() => setActiveIdx(i)}
                  aria-label={`View clip ${i + 1}`}
                  aria-pressed={i === activeIdx}
                  className={`h-16 w-28 shrink-0 overflow-hidden rounded-md border p-0 ${i === activeIdx ? "border-accent" : "border-line"}`}
                >
                  <video src={c.url} muted className="h-full w-full object-cover" />
                </button>
              ))}
            </div>
          )}

          <div className="field mt-3">
            <div className="flex items-center justify-between gap-3">
              <label htmlFor="qv-feedback" className="text-xs font-semibold text-subtle">
                Feedback (folds into the prompt on the next render)
              </label>
              <RefineButton
                kind="video"
                instruction={feedback}
                onRefined={setFeedback}
                onError={(m) => setMsg({ kind: "error", text: m })}
              />
            </div>
            <textarea
              id="qv-feedback"
              value={feedback}
              onChange={(e) => setFeedback(e.target.value)}
              rows={3}
              placeholder="e.g. slower twirl, warmer light"
            />
          </div>

          <div className="mt-3 flex flex-wrap gap-2">
            <button type="button" className="btn-primary" onClick={download} disabled={busy}>
              <DownloadIcon size={16} /> Save video
            </button>
            {caps && caps.modes.includes("extend") && (
              <button type="button" onClick={startExtend} disabled={busy}>Extend +7s</button>
            )}
          </div>
        </section>
      )}
    </div>
  );
}
