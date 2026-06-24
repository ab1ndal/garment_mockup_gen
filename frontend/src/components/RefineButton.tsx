import { useEffect, useRef, useState } from "react";
import { refinePrompt } from "../api";
import { InfoIcon, SparklesIcon } from "./icons";

const HINTS: Record<"image" | "video", string> = {
  image:
    "Describe what you want — garment, mood, any must-keep details. " +
    "e.g. 'Festive Diwali saree, warm mood — match the provided pattern details.'",
  video:
    "Describe the clip — motion, camera, mood, must-keep details. " +
    "e.g. 'Slow elegant twirl, soft festive light, fabric flowing — keep the print exact.'",
};

function InfoTooltip({ kind }: { kind: "image" | "video" }) {
  const [open, setOpen] = useState(false);
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const tipId = `refine-hint-${kind}`;

  useEffect(() => {
    if (!open) return;
    const onDocClick = (e: MouseEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", onDocClick);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDocClick);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <div
      className="tt-wrap"
      ref={wrapRef}
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
    >
      <button
        type="button"
        className="tt-trigger"
        aria-label="How to write a refine instruction"
        aria-expanded={open}
        aria-describedby={open ? tipId : undefined}
        onClick={() => setOpen((v) => !v)}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
      >
        <InfoIcon size={18} />
      </button>
      {open && (
        <div id={tipId} role="tooltip" className="tt-bubble">
          {HINTS[kind]}
        </div>
      )}
    </div>
  );
}

export default function RefineButton({
  kind, instruction, categoryid, onRefined, onError,
}: {
  kind: "image" | "video";
  instruction: string;
  categoryid?: string;
  onRefined: (text: string) => void;
  onError: (msg: string) => void;
}) {
  const [busy, setBusy] = useState(false);
  const empty = !instruction.trim();

  const run = () => {
    setBusy(true);
    refinePrompt(instruction, categoryid, kind)
      .then((r) => onRefined(r.refined))
      .catch((e) => onError(e.message))
      .finally(() => setBusy(false));
  };

  return (
    <div className="toolbar" style={{ alignItems: "center", gap: "var(--sp-2)" }}>
      <button className="btn-primary" onClick={run} disabled={busy || empty}>
        {busy ? <span className="spinner" aria-hidden /> : <SparklesIcon />}
        {busy ? "Refining…" : "Refine"}
      </button>
      <InfoTooltip kind={kind} />
    </div>
  );
}
