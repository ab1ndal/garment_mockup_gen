import { useState } from "react";
import { refinePrompt } from "../api";

const HINTS: Record<"image" | "video", string> = {
  image:
    "Describe what you want — garment, mood, any must-keep details. " +
    "e.g. 'Festive Diwali saree, warm mood — match the provided pattern details.'",
  video:
    "Describe the clip — motion, camera, mood, must-keep details. " +
    "e.g. 'Slow elegant twirl, soft festive light, fabric flowing — keep the print exact.'",
};

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
        {busy && <span className="spinner" aria-hidden />}
        {busy ? "Refining…" : "✨ Refine"}
      </button>
      <span
        className="hint"
        role="img"
        aria-label="How to write a refine instruction"
        title={HINTS[kind]}
        style={{ cursor: "help" }}
      >
        ⓘ
      </span>
    </div>
  );
}
