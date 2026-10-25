import { useEffect, useState } from "react";
import {
  type ApiError, type DescriptionDraft, type ProductDescription,
  draftBackfillDescription, getBackfillDescription,
} from "../api";

const NOTE_MAX = 500;
const WORDS_MIN = 15;
const WORDS_MAX = 60;

const SOURCE_LABEL: Record<string, string> = {
  manual: "Manual",
  ai: "AI (not reviewed)",
  ai_reviewed: "AI-reviewed",
};

function sourceLabel(cur: ProductDescription): string {
  if (!cur.description_source) return cur.description ? "Unknown source" : "No description";
  const base = SOURCE_LABEL[cur.description_source] ?? cur.description_source;
  return cur.description_reviewed_at
    ? `${base} · ${new Date(cur.description_reviewed_at).toLocaleDateString()}`
    : base;
}

const wordCount = (t: string) => t.trim().split(/\s+/).filter(Boolean).length;

/**
 * The product's storefront description inside a Backfill review. Shows what is
 * in the database on open (no AI call); drafts only on click. The draft lives
 * in the parent (`draft`, null = no draft) because Approve saves it.
 */
export default function DescriptionSection({
  productid, fileId, draft, onDraftChange, onLoaded,
}: {
  productid: string;
  fileId: string;
  draft: string | null;
  onDraftChange: (d: string | null) => void;
  onLoaded: (cur: ProductDescription) => void;
}) {
  const [cur, setCur] = useState<ProductDescription | null>(null);
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [useOriginals, setUseOriginals] = useState(true);
  const [useMockup, setUseMockup] = useState(true);
  const [note, setNote] = useState("");
  const [drafting, setDrafting] = useState(false);
  const [draftErr, setDraftErr] = useState<string | null>(null);
  const [flags, setFlags] = useState<Pick<DescriptionDraft, "uncertain" | "conflicts"> | null>(null);

  useEffect(() => {
    let stale = false;
    getBackfillDescription(productid)
      .then((d) => { if (!stale) { setCur(d); onLoaded(d); } })
      .catch((e: ApiError) => { if (!stale) setLoadErr(e.message); });
    return () => { stale = true; };
    // Keyed on productid only: onLoaded is a fresh parent callback each render.
  }, [productid]);

  const doDraft = () => {
    setDrafting(true);
    setDraftErr(null);
    draftBackfillDescription(fileId, {
      productid, use_originals: useOriginals, use_mockup: useMockup,
      note: note.trim() || undefined,
    })
      .then((d) => {
        onDraftChange(d.description);
        setFlags({ uncertain: d.uncertain, conflicts: d.conflicts });
      })
      .catch((e: ApiError) => setDraftErr(e.message))
      .finally(() => setDrafting(false));
  };

  const discard = () => { onDraftChange(null); setFlags(null); };
  const noSource = !useOriginals && !useMockup;
  // Drafting waits for the current text: approve sends it back as the expected value.
  const words = draft === null ? 0 : wordCount(draft);

  return (
    <section className="stack-sm desc-section" aria-labelledby="bf-desc-label">
      <span className="section-label" id="bf-desc-label">Description</span>

      {loadErr ? (
        <p className="alert alert-error" role="alert">Could not load the description: {loadErr}</p>
      ) : !cur ? (
        <p className="muted">Loading…</p>
      ) : (
        <div className="desc-current">
          <span className="pill">{sourceLabel(cur)}</span>
          <p className="desc-text">{cur.description || <span className="muted">No description yet.</span>}</p>
        </div>
      )}

      <div className="desc-controls">
        <label className="check">
          <input type="checkbox" checked={useOriginals} onChange={(e) => setUseOriginals(e.target.checked)} />
          <span>Drive originals</span>
        </label>
        <label className="check">
          <input type="checkbox" checked={useMockup} onChange={(e) => setUseMockup(e.target.checked)} />
          <span>Mockup</span>
        </label>
        <button type="button" disabled={drafting || noSource || !cur} onClick={doDraft} aria-busy={drafting}>
          {drafting ? "Drafting…" : draft === null ? "Draft description" : "Regenerate"}
        </button>
      </div>
      {noSource && <p className="muted">Pick at least one image source.</p>}

      <div className="field">
        <label htmlFor="bf-desc-note">Note for the draft (optional)</label>
        <input
          id="bf-desc-note"
          value={note}
          maxLength={NOTE_MAX}
          onChange={(e) => setNote(e.target.value)}
          placeholder="e.g. has mirror work on the sleeves"
        />
      </div>

      {draftErr && (
        <p className="alert alert-error" role="alert">{draftErr} Try again, or approve without a description.</p>
      )}

      {draft !== null && (
        <div className="stack-sm">
          {flags && flags.conflicts.length > 0 && (
            <div className="alert alert-info" role="note">
              <strong>Shop text and photos disagree</strong> (left out of the draft):
              <ul>{flags.conflicts.map((c) => <li key={c}>{c}</li>)}</ul>
            </div>
          )}
          {flags && flags.uncertain.length > 0 && (
            <div className="alert alert-info" role="note">
              <strong>Not sure, left out:</strong>
              <ul>{flags.uncertain.map((u) => <li key={u}>{u}</li>)}</ul>
            </div>
          )}
          <div className="field">
            <label htmlFor="bf-desc-draft">AI draft (saved when you approve the image)</label>
            <textarea
              id="bf-desc-draft"
              value={draft}
              rows={4}
              onChange={(e) => onDraftChange(e.target.value)}
              aria-describedby="bf-desc-count"
            />
            <span id="bf-desc-count" className={words < WORDS_MIN || words > WORDS_MAX ? "desc-count desc-count-off" : "desc-count muted"}>
              {words} words (target {WORDS_MIN}–{WORDS_MAX})
            </span>
          </div>
          <div>
            <button type="button" onClick={discard}>Discard draft</button>
          </div>
        </div>
      )}
    </section>
  );
}
