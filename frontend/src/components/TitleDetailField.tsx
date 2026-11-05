import { useEffect, useState } from "react";
import {
  type ApiError, type TitleDraft, type TitleEdit, type TitlePreview,
  getProductTitle, getTitlePreview,
} from "../api";
import CopyDraftStatus from "./CopyDraftStatus";
import { useCopyDraft } from "./useCopyDraft";

export const TITLE_DETAIL_MAX = 60;  // database limit
const PREVIEW_DEBOUNCE_MS = 400;

/**
 * The product's website title detail, on every screen that publishes a photo
 * (a published photo puts the product live). The edit lives in the parent because
 * its publish request sends it. An AI draft fills only an empty field; over text
 * someone wrote it stays a suggestion the reviewer has to choose.
 */
export default function TitleDetailField({ productid, value, onChange, suggestion }: {
  productid: string;
  value: TitleEdit | null;
  onChange: (t: TitleEdit) => void;
  suggestion: TitleDraft | null;
}) {
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [preview, setPreview] = useState<TitlePreview | null>(null);

  const ai = suggestion;
  const edit = value?.productid === productid ? value : null;
  const loaded = edit !== null;

  useEffect(() => {
    // Already loaded when a second field for this product mounts; reloading would drop edits.
    if (value?.productid === productid) return;
    let stale = false;
    getProductTitle(productid)
      .then((t) => {
        if (!stale) onChange({ productid, detail: t.title_detail ?? "", expected: t.title_detail, edited: false });
      })
      .catch((e: ApiError) => { if (!stale) setLoadErr(e.message); });
    return () => { stale = true; };
    // Keyed on productid only: value and onChange change on every edit and render.
  }, [productid]);

  useEffect(() => {
    if (!edit || edit.edited || edit.expected?.trim()) return;
    const next = ai?.title_detail ?? "";
    if (next !== edit.detail) onChange({ ...edit, detail: next });
    // Runs when a draft arrives or is discarded, not on each edit.
  }, [ai, loaded]);

  const detail = edit?.detail;
  useEffect(() => {
    if (detail === undefined) return;
    let stale = false;
    const timer = setTimeout(() => {
      getTitlePreview(productid, detail)
        .then((p) => { if (!stale) setPreview(p); })
        // A failed preview must not get in the way of publishing.
        .catch(() => { if (!stale) setPreview(null); });
    }, PREVIEW_DEBOUNCE_MS);
    return () => { stale = true; clearTimeout(timer); };
  }, [productid, detail]);

  const inputId = `title-detail-${productid}`;
  const suggested = ai?.title_detail && edit && ai.title_detail !== edit.detail ? ai.title_detail : null;

  if (loadErr) {
    return <p className="alert alert-error" role="alert">Could not load the title detail: {loadErr}</p>;
  }
  return (
    <div className="stack-sm">
      <div className="field">
        <label htmlFor={inputId}>Title detail for the website (saved when you publish)</label>
        <input
          id={inputId}
          value={edit?.detail ?? ""}
          disabled={!edit}
          maxLength={TITLE_DETAIL_MAX}
          onChange={(e) => edit && onChange({ ...edit, detail: e.target.value, edited: true })}
          placeholder={edit ? "e.g. Zari Border and Tassels" : "Loading…"}
          aria-describedby={`${inputId}-count`}
        />
        <span id={`${inputId}-count`} className="desc-count muted">
          {edit?.detail.length ?? 0}/{TITLE_DETAIL_MAX} characters
        </span>
      </div>

      {suggested && edit && (
        <p className="muted">
          AI suggests: {suggested}{" "}
          <button type="button" onClick={() => onChange({ ...edit, detail: suggested, edited: true })}>
            Use this
          </button>
        </p>
      )}
      {ai?.title_detail_note && <p className="alert alert-info" role="note">{ai.title_detail_note}</p>}
      {ai?.colour_mismatch && (
        <p className="alert alert-info" role="note">
          <strong>Check the recorded colour:</strong> {ai.colour_mismatch} Nothing was changed.
        </p>
      )}

      <p className="muted" aria-live="polite">
        {preview && <>Customers see: <strong>{preview.title}</strong></>}
        {preview && !detail?.trim() && " Other products may share this. Add a detail to make it unique."}
      </p>
      {preview?.duplicate && (
        <p className="alert alert-info" role="alert">
          Another product already has this title. You can still publish, but add something specific if you can.
        </p>
      )}
    </div>
  );
}

/**
 * The field for screens with no description panel. Mounting it asks for the product's
 * AI draft, so mount it only once an image is chosen. These screens save only the title.
 */
export function AutoTitleDetailField({ productid, value, onChange }: {
  productid: string;
  value: TitleEdit | null;
  onChange: (t: TitleEdit) => void;
}) {
  const ai = useCopyDraft({ kind: "product", productid });
  return (
    <div className="stack-sm">
      <TitleDetailField productid={productid} value={value} onChange={onChange}
        suggestion={ai.draft?.status === "ready" ? ai.draft : null} />
      <CopyDraftStatus draft={ai.draft} error={ai.error} asking={ai.asking} card={ai.card}
        onRetry={() => ai.ask({ force: true })} />
    </div>
  );
}
