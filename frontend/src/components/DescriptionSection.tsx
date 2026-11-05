import { useEffect, useRef, useState } from "react";
import {
  type ApiError, type CopyDraft, type DraftSource, type ProductDescription, type ProductTags,
  type ProposedTag, type TagPick, type TagRow, type TitleDraft, type TitleEdit,
  getBackfillDescription, getBackfillTags,
} from "../api";
import CopyDraftStatus from "./CopyDraftStatus";
import TagEditor, { CurrentTags } from "./TagEditor";
import TitleDetailField from "./TitleDetailField";
import { useCopyDraft } from "./useCopyDraft";

const NOTE_MAX = 500;
const DESCRIPTION_MAX = 600;  // backend cap; longer text is not saved
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

// Proposals start selected, except where a hand-entered row already covers them:
// the save keeps manual rows, and re-sending one would collide with it.
function initialPicks(proposed: ProposedTag[], manual: TagRow[], single: Set<string>): TagPick[] {
  return proposed
    .filter((t) => !manual.some((m) => m.group === t.group && (m.value === t.value || single.has(t.group))))
    .map((t) => ({ group: t.group, value: t.value, source: "ai_reviewed", confidence: t.confidence }));
}

/**
 * The product's title, description and tags inside a review. Opening it asks for an
 * AI draft, which fills in by itself so the reviewer only has to check it; nothing is
 * saved until they approve the image. Text a person wrote is never replaced: the AI
 * version waits beside it as a choice. The values live in the parent (`useReviewCopy`)
 * because Approve sends them.
 */
export default function DescriptionSection({
  productid, source, draft, onDraftChange, descriptionEdited, onDescriptionEdited,
  onLoaded, tags, onTagsChange, title, onTitleChange,
}: {
  productid: string;
  source: DraftSource;
  draft: string | null;
  onDraftChange: (d: string | null) => void;
  descriptionEdited: boolean;
  onDescriptionEdited: (edited: boolean) => void;
  onLoaded: (cur: ProductDescription) => void;
  tags: TagPick[] | null;
  onTagsChange: (t: TagPick[] | null) => void;
  title: TitleEdit | null;
  onTitleChange: (t: TitleEdit) => void;
}) {
  const [cur, setCur] = useState<ProductDescription | null>(null);
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [useOriginals, setUseOriginals] = useState(true);
  const [useMockup, setUseMockup] = useState(true);
  const [note, setNote] = useState("");
  const [tagInfo, setTagInfo] = useState<ProductTags | null>(null);
  const [tagErr, setTagErr] = useState<string | null>(null);
  const [proposed, setProposed] = useState<ProposedTag[]>([]);
  const [titleDraft, setTitleDraft] = useState<TitleDraft | null>(null);
  // The AI description when it could not be filled in: someone wrote the current text,
  // the reviewer has edited, or they discarded it. Using it costs nothing.
  const [offered, setOffered] = useState<CopyDraft | null>(null);
  const applied = useRef<number | null>(null);
  const ai = useCopyDraft(source);

  useEffect(() => {
    let stale = false;
    getBackfillDescription(productid)
      .then((d) => { if (!stale) { setCur(d); onLoaded(d); } })
      .catch((e: ApiError) => { if (!stale) setLoadErr(e.message); });
    getBackfillTags(productid)
      .then((t) => { if (!stale) setTagInfo(t); })
      .catch((e: ApiError) => { if (!stale) setTagErr(e.message); });
    return () => { stale = true; };
    // Keyed on productid only: onLoaded is a fresh parent callback each render.
  }, [productid]);

  const manual = (tagInfo?.current ?? []).filter((t) => t.source === "manual");
  const singleGroups = new Set((tagInfo?.groups ?? []).filter((g) => g.single).map((g) => g.group));

  const take = (d: CopyDraft) => {
    onDraftChange(d.description ?? "");
    // Without the vocab there is nothing to edit against, so tags stay untouched.
    onTagsChange(tagInfo && tagInfo.groups.length > 0 ? initialPicks(d.tags, manual, singleGroups) : null);
    setOffered(null);
  };

  // Waits for the current text, which decides fill or offer and which Approve sends
  // back as the expected value, and for the tags, which the picks are built from.
  useEffect(() => {
    const d = ai.draft;
    if (d?.status !== "ready" || d.id === null || !cur || !(tagInfo || tagErr) || applied.current === d.id) return;
    applied.current = d.id;
    setProposed(d.tags);
    setTitleDraft(d);
    if (cur.replaceable && !descriptionEdited) {
      take(d);
    } else {
      setOffered(d);
    }
  }, [ai.draft, cur, tagInfo, tagErr]);

  const flags = ai.draft?.status === "ready" ? ai.draft : null;
  const inFlight = ai.asking || ai.draft?.status === "queued" || ai.draft?.status === "drafting";
  const noSource = !useOriginals && !useMockup;
  const discard = () => {
    if (flags) setOffered(flags);
    onDraftChange(null);
    onDescriptionEdited(false);
    setTitleDraft(null);
    onTagsChange(null);
  };
  const askAgain = () => ai.ask({
    force: true, use_originals: useOriginals, use_mockup: useMockup, note: note.trim() || undefined,
  });
  const words = draft === null ? 0 : wordCount(draft);

  return (
    <section className="stack-sm desc-section" aria-labelledby="bf-desc-label">
      <TitleDetailField productid={productid} value={title} onChange={onTitleChange} suggestion={titleDraft} />

      <span className="section-label" id="bf-desc-label">Description</span>

      <CopyDraftStatus draft={ai.draft} error={ai.error} asking={ai.asking} card={ai.card} onRetry={askAgain} />

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

      <div className="stack-sm">
        <span className="section-label" id="bf-tags-label">Tags</span>
        {tagErr ? (
          <p className="alert alert-error" role="alert">Could not load the tags: {tagErr}</p>
        ) : !tagInfo ? (
          <p className="muted">Loading…</p>
        ) : tagInfo.groups.length === 0 ? (
          <p className="muted">No tags for this category.</p>
        ) : (
          <CurrentTags groups={tagInfo.groups} current={tagInfo.current} />
        )}
      </div>

      {offered?.description && (
        <div className="alert alert-info" role="note">
          <p><strong>AI suggests:</strong> {offered.description}</p>
          <button type="button" onClick={() => { take(offered); setTitleDraft(offered); onDescriptionEdited(true); }}>
            Use this
          </button>
        </div>
      )}

      {draft === null && (
        <div>
          <button type="button" disabled={!cur}
            onClick={() => { onDraftChange(cur?.description ?? ""); onDescriptionEdited(true); }}>
            Type a description
          </button>
        </div>
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
            <label htmlFor="bf-desc-draft">New description (saved when you approve the image)</label>
            <textarea
              id="bf-desc-draft"
              value={draft}
              rows={4}
              maxLength={DESCRIPTION_MAX}
              onChange={(e) => { onDraftChange(e.target.value); onDescriptionEdited(true); }}
              aria-describedby="bf-desc-count"
            />
            <span id="bf-desc-count" className={words < WORDS_MIN || words > WORDS_MAX ? "desc-count desc-count-off" : "desc-count muted"}>
              {words} words (target {WORDS_MIN}–{WORDS_MAX})
            </span>
          </div>
          {tags !== null && tagInfo && tagInfo.groups.length > 0 && (
            <div className="stack-sm" aria-labelledby="bf-tags-draft-label">
              <span className="section-label" id="bf-tags-draft-label">
                Tags for this draft (saved when you approve the image)
              </span>
              <TagEditor groups={tagInfo.groups} manual={manual} proposed={proposed}
                picks={tags} onChange={onTagsChange} />
            </div>
          )}
          <div>
            <button type="button" onClick={discard}>Keep the current description</button>
          </div>
        </div>
      )}

      {ai.draft?.status !== "unavailable" && (
        <details>
          <summary>Ask the AI again</summary>
          <div className="stack-sm">
            <div className="desc-controls">
              <label className="check">
                <input type="checkbox" checked={useOriginals} onChange={(e) => setUseOriginals(e.target.checked)} />
                <span>Drive originals</span>
              </label>
              <label className="check">
                <input type="checkbox" checked={useMockup} onChange={(e) => setUseMockup(e.target.checked)} />
                <span>Mockup</span>
              </label>
              <button type="button" disabled={inFlight || noSource} onClick={askAgain} aria-busy={inFlight}>
                {inFlight ? "Writing…" : "Write again"}
              </button>
            </div>
            {noSource && <p className="muted">Pick at least one image source.</p>}
            <div className="field">
              <label htmlFor="bf-desc-note">Note for the AI (optional)</label>
              <input
                id="bf-desc-note"
                value={note}
                maxLength={NOTE_MAX}
                onChange={(e) => setNote(e.target.value)}
                placeholder="e.g. has mirror work on the sleeves"
              />
            </div>
          </div>
        </details>
      )}
    </section>
  );
}
