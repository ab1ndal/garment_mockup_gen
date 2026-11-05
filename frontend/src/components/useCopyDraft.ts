import { useEffect, useRef, useState } from "react";
import {
  type ApiError, type CopyDraft, type CopyDraftOptions, type DraftSource,
  getCopyDraft, requestCopyDraft,
} from "../api";

const POLL_MS = 2500;

/** Which card asked. Matches the server's ``source_card``. */
export const cardOf = (s: DraftSource): string =>
  s.kind === "backfill" ? `backfill:${s.fileId}` : s.kind === "batch" ? `batch:${s.itemId}` : "product";

/**
 * The product's AI draft. Mounting this asks for one, with no click: mount it only
 * where a reviewer has opened a card or chosen an image. The server decides whether
 * that costs anything, and a second ask for the same draft does not pay twice.
 */
export function useCopyDraft(source: DraftSource) {
  const [draft, setDraft] = useState<CopyDraft | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [asking, setAsking] = useState(false);
  const generation = useRef(0);
  const key = JSON.stringify(source);

  const ask = (options: CopyDraftOptions) => {
    const mine = ++generation.current;
    setAsking(true);
    setError(null);
    requestCopyDraft(source, options)
      .then((d) => { if (mine === generation.current) setDraft(d); })
      .catch((e: ApiError) => { if (mine === generation.current) setError(e.message); })
      .finally(() => { if (mine === generation.current) setAsking(false); });
  };

  useEffect(() => {
    setDraft(null);
    ask({});
    return () => { generation.current++; };
    // Keyed on the source's value: `source` is a fresh object each render.
  }, [key]);

  const status = draft?.status;
  const id = draft?.id;
  useEffect(() => {
    if (status !== "queued" && status !== "drafting") return;
    let stale = false;
    const timer = setTimeout(() => {
      getCopyDraft(source.productid)
        .then((d) => { if (!stale) setDraft(d); })
        .catch((e: ApiError) => { if (!stale) setError(e.message); });
    }, POLL_MS);
    return () => { stale = true; clearTimeout(timer); };
    // `draft` changes identity on every poll, which is what schedules the next one.
  }, [status, id, draft]);

  return { draft, error, asking, ask, card: cardOf(source) };
}
