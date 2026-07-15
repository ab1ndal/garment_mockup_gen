import { useCallback, useEffect, useRef, useState } from "react";
import {
  ApiError, getCategories,
  enqueueBatch, listBatchItems, getBatchCounts, getBatchSources,
  acceptBatch, editBatch, rejectBatch, retryBatch,
  type BatchItem, type BatchTabId, type BatchSources, type Category,
} from "../api";
import { useImageLightbox } from "./Lightbox";

const PAGE = 20;

const TABS: { id: BatchTabId; label: string; statuses: string[] }[] = [
  { id: "ready", label: "Ready", statuses: ["ready"] },
  { id: "in_progress", label: "In progress", statuses: ["queued", "generating"] },
  { id: "failed", label: "Failed", statuses: ["failed"] },
  { id: "history", label: "History", statuses: ["published", "rejected"] },
];

const EMPTY_COPY: Record<BatchTabId, string> = {
  ready: "Nothing ready to review yet.",
  in_progress: "Nothing generating right now.",
  failed: "No failed cards.",
  history: "No published or rejected cards yet.",
};

const gridStyle: React.CSSProperties = {
  display: "grid",
  gridTemplateColumns: "repeat(auto-fill, minmax(200px, 1fr))",
  gap: "var(--sp-4)",
};

function countFor(tabId: BatchTabId, counts: Record<string, number>): number {
  const t = TABS.find((x) => x.id === tabId)!;
  return t.statuses.reduce((n, s) => n + (counts[s] || 0), 0);
}

export default function BatchTab() {
  const [cats, setCats] = useState<Category[]>([]);
  const [category, setCategory] = useState<string>("");
  const [count, setCount] = useState<number>(10);
  const [enqueuing, setEnqueuing] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const [tab, setTab] = useState<BatchTabId>("ready");
  const [items, setItems] = useState<BatchItem[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [counts, setCounts] = useState<Record<string, number>>({});
  const [loading, setLoading] = useState(false);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [review, setReview] = useState<BatchItem | null>(null);

  const lightbox = useImageLightbox();

  useEffect(() => { getCategories().then(setCats).catch(() => {}); }, []);

  const loadCounts = useCallback(() => {
    getBatchCounts().then((r) => setCounts(r.counts)).catch(() => {});
  }, []);

  const load = useCallback((t: BatchTabId, off: number) => {
    setLoading(true);
    setError(null);
    listBatchItems({ tab: t, offset: off, limit: PAGE })
      .then((r) => { setItems(r.items); setTotal(r.total); setOffset(r.offset); })
      .catch((e) => setError(e instanceof ApiError ? e.message : "Failed to load."))
      .finally(() => setLoading(false));
  }, []);

  // Items are fetched 20 at a time, ONLY on tab/page change (and manual refresh
  // or after an action) — no background polling of the list.
  useEffect(() => { load(tab, 0); loadCounts(); }, [tab, load, loadCounts]);

  const refresh = () => { load(tab, offset); loadCounts(); };

  async function onEnqueue() {
    setEnqueuing(true); setNotice(null); setError(null);
    try {
      const r = await enqueueBatch({ category: category || null, count });
      const skips = r.skipped.length ? ` · skipped ${r.skipped.length}` : "";
      setNotice(`Queued ${r.queued} card(s)${skips}.`);
      setTab("in_progress"); loadCounts();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Enqueue failed.");
    } finally {
      setEnqueuing(false);
    }
  }

  function afterAction(id: number) {
    setItems((xs) => xs.filter((x) => x.id !== id));
    setTotal((t) => Math.max(0, t - 1));
    loadCounts();
  }

  async function run(id: number, fn: () => Promise<{ warning: string | null }>) {
    setBusyId(id); setNotice(null);
    try {
      const r = await fn();
      afterAction(id);
      setReview(null);
      if (r.warning) setNotice(r.warning);
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        setNotice("This card was already handled.");
        afterAction(id);
        setReview(null);
      } else {
        setError(e instanceof ApiError ? e.message : "Action failed.");
      }
    } finally {
      setBusyId(null);
    }
  }

  const start = total === 0 ? 0 : offset + 1;
  const end = offset + items.length;

  return (
    <div className="stack">
      <div className="toolbar" style={{ alignItems: "flex-end", gap: "var(--sp-3)", flexWrap: "wrap" }}>
        <div className="field">
          <label htmlFor="batch-category">Category</label>
          <select id="batch-category" value={category} onChange={(e) => setCategory(e.target.value)}>
            <option value="">All categories</option>
            {cats.map((c) => <option key={c.categoryid} value={c.categoryid}>{c.name}</option>)}
          </select>
        </div>
        <div className="field">
          <label htmlFor="batch-count">Products</label>
          <input id="batch-count" type="number" min={1} max={100} value={count}
                 onChange={(e) => setCount(Math.max(1, Math.min(100, Number(e.target.value) || 1)))} />
        </div>
        <button className="btn-primary" onClick={onEnqueue} disabled={enqueuing}>
          {enqueuing ? "Queuing…" : "Generate"}
        </button>
        <button onClick={refresh} disabled={loading}>Refresh</button>
      </div>

      {notice && <p className="alert" role="status">{notice}</p>}
      {error && <p className="alert alert-error" role="alert">{error}</p>}

      <div className="tabs subtabs" role="tablist" aria-label="Batch review queues">
        {TABS.map((t) => (
          <button key={t.id} className="tab" role="tab" aria-selected={tab === t.id}
                  onClick={() => setTab(t.id)}>
            {t.label}
            <span className="tab-count" aria-label={`${countFor(t.id, counts)} items`}>
              {countFor(t.id, counts)}
            </span>
          </button>
        ))}
      </div>

      <div role="tabpanel" className="stack">
        <span className="muted">
          {loading ? "Loading…" : total === 0 ? "0 items" : `${start}–${end} of ${total}`}
        </span>

        {loading ? (
          <p className="muted">Loading cards…</p>
        ) : items.length === 0 ? (
          <p className="muted">{EMPTY_COPY[tab]}</p>
        ) : (
          <>
            <div style={gridStyle}>
              {items.map((it) => (
                <Card key={it.id} item={it}
                      onEnlarge={() => it.generated_thumb_url && it.drive_file_id
                        && lightbox.showDrive(it.drive_file_id, it.productid, it.generated_thumb_url ?? "")}>
                  {it.status === "ready" && (
                    <>
                      <button className="btn-primary" disabled={busyId === it.id}
                              onClick={() => setReview(it)}>Review</button>
                      <button disabled={busyId === it.id}
                              onClick={() => run(it.id, () => acceptBatch(it.id))}>Accept</button>
                      <button className="btn-danger" disabled={busyId === it.id}
                              onClick={() => run(it.id, () => rejectBatch(it.id))}>Reject</button>
                    </>
                  )}
                  {it.status === "failed" && (
                    <button disabled={busyId === it.id}
                            onClick={() => run(it.id, () => retryBatch(it.id))}>
                      {busyId === it.id ? "Retrying…" : "Retry"}
                    </button>
                  )}
                  {(it.status === "queued" || it.status === "generating") && (
                    <span className="pill pill-pending">{it.status}</span>
                  )}
                  {(it.status === "published" || it.status === "rejected") && (
                    <span className="pill">{it.status}</span>
                  )}
                </Card>
              ))}
            </div>

            {total > PAGE && (
              <div className="toolbar" style={{ justifyContent: "center", alignItems: "center", gap: "var(--sp-3)" }}>
                <button disabled={offset === 0 || loading} onClick={() => load(tab, Math.max(0, offset - PAGE))}>
                  ‹ Prev
                </button>
                <span className="muted">Page {Math.floor(offset / PAGE) + 1} of {Math.max(1, Math.ceil(total / PAGE))}</span>
                <button disabled={end >= total || loading} onClick={() => load(tab, offset + PAGE)}>
                  Next ›
                </button>
              </div>
            )}
          </>
        )}
      </div>

      {review && (
        <ReviewModal
          item={review}
          busy={busyId === review.id}
          onClose={() => setReview(null)}
          onAccept={(c) => run(review.id, () => acceptBatch(review.id, { color: c }))}
          onEdit={(note, ids) => run(review.id, () => editBatch(review.id, { prompt_note: note, image_ids: ids }))}
          onReject={() => run(review.id, () => rejectBatch(review.id))}
        />
      )}

      {lightbox.node}
    </div>
  );
}

function Card({ item, onEnlarge, children }: {
  item: BatchItem;
  onEnlarge: () => void;
  children: React.ReactNode;
}) {
  return (
    <div className="card bf-card stack-sm">
      <div className="bf-thumb">
        {item.generated_thumb_url ? (
          <button type="button" className="img-zoom" onClick={onEnlarge}
                  aria-label={`Enlarge mockup ${item.productid}`}>
            <img src={item.generated_thumb_url} alt={`Mockup ${item.productid}`} loading="lazy" />
          </button>
        ) : (
          <div className="bf-thumb-empty">{item.status === "failed" ? "failed" : item.status}</div>
        )}
      </div>
      <div className="toolbar" style={{ justifyContent: "space-between", alignItems: "center", gap: "var(--sp-2)" }}>
        <strong className="bf-card-title mono" title={item.productid}>{item.productid}</strong>
        {item.color && <span className="pill">{item.color}</span>}
      </div>
      {item.error && <p className="alert alert-error" role="note" style={{ fontSize: "var(--fs-xs)" }}>{item.error}</p>}
      <div className="toolbar bf-card-actions">{children}</div>
    </div>
  );
}

function Modal({ title, onClose, children }: {
  title: string; onClose: () => void; children: React.ReactNode;
}) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const prevFocus = document.activeElement as HTMLElement | null;
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    ref.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") { onClose(); return; }
      if (e.key !== "Tab" || !ref.current) return;
      const f = ref.current.querySelectorAll<HTMLElement>(
        'a[href],button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex="-1"])',
      );
      if (f.length === 0) return;
      const first = f[0];
      const last = f[f.length - 1];
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = prevOverflow;
      prevFocus?.focus?.();
    };
  }, [onClose]);

  return (
    <div className="modal-overlay" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="modal" role="dialog" aria-modal="true" aria-label={title} tabIndex={-1} ref={ref}>
        {children}
      </div>
    </div>
  );
}

function ReviewModal(props: {
  item: BatchItem; busy: boolean; onClose: () => void;
  onAccept: (color: string | null) => void;
  onEdit: (note: string, imageIds: string[]) => void;
  onReject: () => void;
}) {
  const { item, busy, onClose, onAccept, onEdit, onReject } = props;
  const [src, setSrc] = useState<BatchSources | null>(null);
  const [note, setNote] = useState("");
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [color, setColor] = useState<string | null>(item.color);
  const [msg, setMsg] = useState<string | null>(null);
  const lightbox = useImageLightbox();

  useEffect(() => {
    getBatchSources(item.id)
      .then((s) => { setSrc(s); setPicked(new Set(s.image_ids)); setColor(s.color); })
      .catch((e) => setMsg(e instanceof ApiError ? e.message : "Failed to load sources."));
  }, [item.id]);

  function toggle(id: string) {
    setPicked((p) => { const n = new Set(p); n.has(id) ? n.delete(id) : n.add(id); return n; });
  }

  const title = item.productid + (item.color ? ` · ${item.color}` : "");

  return (
    <Modal title={`Review ${title}`} onClose={onClose}>
      <div className="modal-head">
        <h3><span className="mono">{item.productid}</span>
          {item.color && <span className="modal-sub">{item.color}</span>}</h3>
        <button className="icon-btn" onClick={onClose} aria-label="Close review">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor"
            strokeWidth="2" strokeLinecap="round" aria-hidden="true">
            <path d="M18 6 6 18M6 6l12 12" />
          </svg>
        </button>
      </div>

      <div className="modal-body">
        <div className="split">
          <div className="stack-sm">
            <span className="section-label">Source images</span>
            {!src ? <p className="muted">Loading…</p> : (
              <div style={gridStyle}>
                {src.sources.map((s) => (
                  <div key={s.id} className={`batch-src ${picked.has(s.id) ? "is-selected" : ""}`}>
                    <button type="button" className="img-frame img-zoom"
                            onClick={() => lightbox.show(s.data_uri, s.id)}
                            aria-label={`Enlarge source ${s.id}`}>
                      <img src={s.data_uri} alt="source" loading="lazy" />
                    </button>
                    <label className="check batch-src-check">
                      <input type="checkbox" checked={picked.has(s.id)} onChange={() => toggle(s.id)} />
                      <span>Use</span>
                    </label>
                  </div>
                ))}
              </div>
            )}
          </div>
          <div className="stack-sm">
            <span className="section-label">Generated</span>
            {src?.generated_preview ? (
              <button type="button" className="img-frame img-zoom"
                      onClick={() => lightbox.show(src.generated_preview!, "generated mockup")}
                      aria-label="Enlarge generated mockup">
                <img src={src.generated_preview} alt="generated mockup" />
              </button>
            ) : <p className="muted">No preview.</p>}
          </div>
        </div>

        {src && src.colors.length > 0 && (
          <div className="field">
            <label htmlFor="batch-color">Color</label>
            <select id="batch-color" value={color ?? ""} onChange={(e) => setColor(e.target.value || null)}>
              <option value="">— no color —</option>
              {src.colors.map((c) => <option key={c} value={c}>{c}</option>)}
            </select>
          </div>
        )}

        <div className="field">
          <label htmlFor="batch-note">Revision note</label>
          <textarea id="batch-note" value={note} onChange={(e) => setNote(e.target.value)} rows={2}
                    placeholder="What to change, for Edit → regenerate… (optional)" />
        </div>

        {msg && <p className="alert alert-error" role="alert">{msg}</p>}
      </div>

      <div className="modal-foot">
        <div className="modal-foot-group">
          <button className="btn-danger" disabled={busy} onClick={onReject}>Reject</button>
        </div>
        <div className="modal-foot-group">
          <button disabled={busy || !note.trim()} onClick={() => onEdit(note, Array.from(picked))}>
            Edit &amp; regenerate
          </button>
          <button className="btn-primary" disabled={busy} onClick={() => onAccept(color)}>
            Accept &amp; publish
          </button>
        </div>
      </div>

      {lightbox.node}
    </Modal>
  );
}
