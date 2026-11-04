import type { CopyDraft } from "../api";

/** One plain line about the AI draft, written for staff. A slow or failed draft never
 *  stops the image being approved, and this says so. */
export default function CopyDraftStatus({ draft, error, asking, card, onRetry }: {
  draft: CopyDraft | null;
  error: string | null;
  asking: boolean;
  card: string;
  onRetry: () => void;
}) {
  const failure = error ?? (draft?.status === "failed" ? draft.error : null);
  if (failure) {
    return (
      <div className="alert alert-error" role="alert">
        <p>AI draft failed: {failure}</p>
        <button type="button" disabled={asking} onClick={onRetry}>Retry</button>
      </div>
    );
  }
  if (asking || draft?.status === "queued" || draft?.status === "drafting") {
    return (
      <p className="muted" aria-live="polite">
        AI is writing the title and description. You can still approve the image now, but the AI text will not be saved with it.
      </p>
    );
  }
  if (draft?.status === "unavailable") {
    return <p className="muted">AI drafts are not switched on yet. Type the title and description yourself.</p>;
  }
  if (draft?.status === "ready" && draft.source_card && draft.source_card !== card) {
    return <p className="muted">Written from another photo of this product.</p>;
  }
  return null;
}
