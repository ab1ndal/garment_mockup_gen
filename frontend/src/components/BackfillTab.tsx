import { useCallback, useEffect, useRef, useState } from "react";
import {
  listBackfill, getBackfillCounts, rescanBackfill, getBackfillSources,
  approveBackfill, flagBackfill, flagEditBackfill, skipBackfill, unskipBackfill,
  type BackfillItem, type BackfillSources, type BackfillStatus, ApiError,
} from "../api";
import { useImageLightbox } from "./Lightbox";

const PAGE = 20;
const ASPECTS = ["1:1", "4:5", "3:4", "9:16", "16:9"];

const TABS: { status: BackfillStatus; label: string }[] = [
  { status: "pending", label: "To review" },
  { status: "skipped", label: "Skipped" },
  { status: "edit", label: "Edits" },
  { status: "regenerate", label: "Regenerate" },
];

const EMPTY_COPY: Record<BackfillStatus, string> = {
  pending: "Nothing left to review.",
  skipped: "No skipped mockups.",
  edit: "Nothing flagged for edits.",
  regenerate: "Nothing flagged for regeneration.",
};

type Dest = "approved" | "edit" | "regenerate" | "skipped" | "pending";

const gridStyle: React.CSSProperties = {
  display: "grid",
  gridTemplateColumns: "repeat(auto-fill, minmax(200px, 1fr))",
  gap: "var(--sp-4)",
};

export default function BackfillTab() {
  const [status, setStatus] = useState<BackfillStatus>("pending");
  const [items, setItems] = useState<BackfillItem[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [counts, setCounts] = useState<Record<BackfillStatus, number> | null>(null);
  const [active, setActive] = useState<BackfillItem | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [rescanning, setRescanning] = useState(false);
  const lightbox = useImageLightbox();

  const loadCounts = useCallback(() => {
    getBackfillCounts().then((r) => setCounts(r.counts)).catch(() => {});
  }, []);

  const load = useCallback((s: BackfillStatus, off: number) => {
    setLoading(true);
    setError(null);
    listBackfill({ status: s, offset: off, limit: PAGE })
      .then((r) => {
        setItems(r.items);
        setTotal(Number.isFinite(r.total) ? r.total : 0);
        setOffset(Number.isFinite(r.offset) ? r.offset : off);
      })
      .catch((e: ApiError) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => { load(status, 0); }, [status, load]);
  useEffect(() => { loadCounts(); }, [loadCounts]);

  const reload = () => load(status, offset);

  // After an action: optimistically drop the card + adjust counts. If the page
  // empties (but more rows exist), refetch to backfill it from the DB.
  const afterAction = (fileId: string, from: BackfillStatus, to?: BackfillStatus | "published") => {
    setCounts((c) => {
      if (!c) return c;
      const next = { ...c, [from]: Math.max(0, c[from] - 1) };
      if (to && to !== "published") next[to] = (next[to] ?? 0) + 1;
      return next;
    });
    setItems((xs) => {
      const left = xs.filter((i) => i.file_id !== fileId);
      if (left.length === 0 && total - 1 > 0) load(status, offset);
      return left;
    });
    setTotal((t) => Math.max(0, t - 1));
  };

  // Centralised mutation runner: handles 409 (someone else got there first) by
  // re-syncing the page + counts, and surfaces any non-fatal warning.
  const run = (
    it: BackfillItem,
    fn: () => Promise<{ warning?: string | null }>,
    to: BackfillStatus | "published",
  ) => {
    setBusyId(it.file_id);
    setNotice(null);
    return fn()
      .then((r) => {
        afterAction(it.file_id, status, to);
        if (r?.warning) setNotice(r.warning);
      })
      .catch((e: ApiError) => {
        if (e.status === 409) {
          setNotice("That mockup was just handled by another reviewer.");
          reload();
          loadCounts();
        } else {
          setError(e.message);
        }
      })
      .finally(() => setBusyId(null));
  };

  const onSkip = (it: BackfillItem) =>
    run(it, () => skipBackfill({ file_id: it.file_id, productid: it.productid }), "skipped");
  const onUnskip = (it: BackfillItem) =>
    run(it, () => unskipBackfill({ file_id: it.file_id, productid: it.productid }), "pending");

  const onResolved = (fileId: string, dest: Dest) => {
    const to: BackfillStatus | "published" =
      dest === "approved" ? "published" : dest;
    afterAction(fileId, status, to);
    setActive(null);
  };

  const onRescan = () => {
    setRescanning(true);
    setNotice(null);
    rescanBackfill()
      .then((r) => {
        setNotice(`Re-synced ${r.synced} mockups from Drive.`);
        load(status, 0);
        loadCounts();
      })
      .catch((e: ApiError) => setError(e.message))
      .finally(() => setRescanning(false));
  };

  const start = total === 0 ? 0 : offset + 1;
  const end = offset + items.length;

  return (
    <div className="stack">
      <div className="tabs subtabs" role="tablist" aria-label="Backfill review queues">
        {TABS.map((t) => (
          <button
            key={t.status}
            className="tab"
            role="tab"
            aria-selected={status === t.status}
            onClick={() => setStatus(t.status)}
          >
            {t.label}
            {counts && (
              <span className="tab-count" aria-label={`${counts[t.status] ?? 0} items`}>
                {counts[t.status] ?? 0}
              </span>
            )}
          </button>
        ))}
      </div>

      <div role="tabpanel" className="stack">
        <div className="toolbar" style={{ justifyContent: "space-between", alignItems: "center" }}>
          <span className="muted">
            {loading ? "Loading…" : total === 0 ? "0 items" : `${start}–${end} of ${total}`}
          </span>
          <div className="toolbar" style={{ gap: "var(--sp-2)" }}>
            <button onClick={reload} disabled={loading}>Refresh</button>
            <button onClick={onRescan} disabled={rescanning}>
              {rescanning ? "Rescanning…" : "Rescan Drive"}
            </button>
          </div>
        </div>

        {notice && <p className="alert" role="status">{notice}</p>}

        {error ? (
          <div className="stack">
            <p className="alert alert-error" role="alert">{error}</p>
            <button onClick={reload}>Retry</button>
          </div>
        ) : loading ? (
          <p className="muted">Loading mockups…</p>
        ) : items.length === 0 ? (
          <p className="muted">{EMPTY_COPY[status]}</p>
        ) : (
          <>
            <div style={gridStyle}>
              {items.map((it) => (
                <Card
                  key={it.file_id}
                  item={it}
                  onEnlarge={(x) => lightbox.showDrive(x.file_id, x.productid ?? x.filename, x.thumbnail_url ?? "")}
                >
                  {status === "pending" && (
                    <>
                      <button className="btn-primary" onClick={() => setActive(it)}>Review</button>
                      <button disabled={busyId === it.file_id} onClick={() => onSkip(it)}>
                        {busyId === it.file_id ? "Skipping…" : "Skip"}
                      </button>
                    </>
                  )}
                  {status === "skipped" && (
                    <>
                      <button className="btn-primary" onClick={() => setActive(it)}>Review</button>
                      <button disabled={busyId === it.file_id} onClick={() => onUnskip(it)}>
                        {busyId === it.file_id ? "Restoring…" : "Unskip"}
                      </button>
                    </>
                  )}
                  {(status === "edit" || status === "regenerate") && (
                    <span className="pill pill-pending">
                      {status === "edit" ? "awaiting edit" : "to regenerate"}
                    </span>
                  )}
                </Card>
              ))}
            </div>

            {total > PAGE && (
              <div className="toolbar" style={{ justifyContent: "center", alignItems: "center", gap: "var(--sp-3)" }}>
                <button disabled={offset === 0 || loading} onClick={() => load(status, Math.max(0, offset - PAGE))}>
                  ‹ Prev
                </button>
                <span className="muted">Page {Math.floor(offset / PAGE) + 1} of {Math.max(1, Math.ceil(total / PAGE))}</span>
                <button disabled={end >= total || loading} onClick={() => load(status, offset + PAGE)}>
                  Next ›
                </button>
              </div>
            )}
          </>
        )}
      </div>

      {active && (
        <ReviewPanel
          item={active}
          status={status}
          onClose={() => setActive(null)}
          onResolved={onResolved}
        />
      )}

      {lightbox.node}
    </div>
  );
}

function Card({ item, onEnlarge, children }: {
  item: BackfillItem;
  onEnlarge: (item: BackfillItem) => void;
  children: React.ReactNode;
}) {
  const label = item.productid ?? item.filename;
  return (
    <div className="card bf-card stack-sm">
      <div className="bf-thumb">
        {item.thumbnail_url ? (
          <button
            type="button"
            className="img-zoom"
            onClick={() => onEnlarge(item)}
            aria-label={`Enlarge generated mockup ${label}`}
          >
            <img
              src={item.thumbnail_url}
              alt={`Generated mockup ${label}`}
              loading="lazy"
            />
          </button>
        ) : (
          <div className="bf-thumb-empty">no preview</div>
        )}
      </div>
      <div className="toolbar" style={{ justifyContent: "space-between", alignItems: "center", gap: "var(--sp-2)" }}>
        <strong className="bf-card-title mono" title={item.productid ?? item.filename}>
          {item.productid ?? item.filename}
        </strong>
        {item.unknown_product && <span className="pill pill-pending">unknown</span>}
      </div>
      <div className="toolbar bf-card-actions">{children}</div>
    </div>
  );
}

function Modal({
  title, onClose, children,
}: {
  title: string;
  onClose: () => void;
  children: React.ReactNode;
}) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const prevFocus = document.activeElement as HTMLElement | null;
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    ref.current?.focus();

    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        onClose();
        return;
      }
      if (e.key !== "Tab" || !ref.current) return;
      const f = ref.current.querySelectorAll<HTMLElement>(
        'a[href],button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex="-1"])',
      );
      if (f.length === 0) return;
      const first = f[0];
      const last = f[f.length - 1];
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = prevOverflow;
      prevFocus?.focus?.();
    };
  }, [onClose]);

  return (
    <div
      className="modal-overlay"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        className="modal"
        role="dialog"
        aria-modal="true"
        aria-label={title}
        tabIndex={-1}
        ref={ref}
      >
        {children}
      </div>
    </div>
  );
}

function ReviewPanel({
  item, status, onClose, onResolved,
}: {
  item: BackfillItem;
  status: BackfillStatus;
  onClose: () => void;
  onResolved: (fileId: string, dest: Dest) => void;
}) {
  const [data, setData] = useState<BackfillSources | null>(null);
  const [colors, setColors] = useState<string[]>([]);
  const [color, setColor] = useState<string>("");
  const [theme, setTheme] = useState("Default");
  const [aspect, setAspect] = useState("1:1");
  const [comment, setComment] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const lightbox = useImageLightbox();

  useEffect(() => {
    getBackfillSources(item.file_id, item.productid)
      .then((d) => {
        setData(d);
        setColors(d.colors);
        if (d.colors.length === 1) setColor(d.colors[0]);
        if (d.suggested_aspect) setAspect(d.suggested_aspect);
      })
      .catch((e: ApiError) => setMsg(e.message));
  }, [item.file_id, item.productid]);

  const onErr = (e: ApiError) =>
    setMsg(e.status === 409 ? "That mockup was just handled by another reviewer." : e.message);

  const doApprove = () => {
    if (!item.productid) return;
    setBusy(true);
    setMsg(null);
    approveBackfill({
      file_id: item.file_id, productid: item.productid,
      color: color || undefined, theme_name: theme, aspect_ratio: aspect,
    })
      .then(() => onResolved(item.file_id, "approved"))
      .catch(onErr)
      .finally(() => setBusy(false));
  };

  const doFlag = () => {
    setBusy(true);
    setMsg(null);
    flagBackfill({ file_id: item.file_id, productid: item.productid })
      .then(() => onResolved(item.file_id, "regenerate"))
      .catch(onErr)
      .finally(() => setBusy(false));
  };

  const doFlagEdit = () => {
    setBusy(true);
    setMsg(null);
    flagEditBackfill({
      file_id: item.file_id, productid: item.productid,
      comment: comment.trim() || undefined,
    })
      .then(() => onResolved(item.file_id, "edit"))
      .catch(onErr)
      .finally(() => setBusy(false));
  };

  const doSkip = () => {
    setBusy(true);
    setMsg(null);
    skipBackfill({ file_id: item.file_id, productid: item.productid })
      .then(() => onResolved(item.file_id, "skipped"))
      .catch(onErr)
      .finally(() => setBusy(false));
  };

  const doUnskip = () => {
    setBusy(true);
    setMsg(null);
    unskipBackfill({ file_id: item.file_id, productid: item.productid })
      .then(() => onResolved(item.file_id, "pending"))
      .catch(onErr)
      .finally(() => setBusy(false));
  };

  const title = item.productid ?? item.filename;

  return (
    <Modal title={`Review ${title}`} onClose={onClose}>
      <div className="modal-head">
        <h3>
          <span className="mono">{title}</span>
          {item.product_name && <span className="modal-sub">{item.product_name}</span>}
          {item.unknown_product && <span className="pill pill-pending">unknown</span>}
        </h3>
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
            <span className="section-label">Original images</span>
            {!data ? (
              <p className="muted">Loading…</p>
            ) : (
              <div style={gridStyle}>
                {[...data.originals.loose, ...data.originals.groups.flatMap((g) => g.images)].map((im) => (
                  <button
                    key={im.id}
                    type="button"
                    className="img-frame img-zoom"
                    onClick={() => lightbox.showDrive(im.id, im.name, im.thumbnail_url)}
                    aria-label={`Enlarge original ${im.name}`}
                  >
                    <img src={im.thumbnail_url} alt={im.name} loading="lazy" />
                  </button>
                ))}
              </div>
            )}
          </div>
          <div className="stack-sm">
            <span className="section-label">Generated</span>
            <button
              type="button"
              className="img-frame img-zoom"
              onClick={() => {
                const src = data ? data.generated_preview : item.thumbnail_url ?? "";
                if (src) lightbox.show(src, `Generated mockup ${title}`);
              }}
              aria-label={`Enlarge generated mockup ${title}`}
            >
              <img
                src={data ? data.generated_preview : item.thumbnail_url ?? ""}
                alt={`Generated mockup ${title}`}
              />
            </button>
          </div>
        </div>

        <div className="review-fields">
          <div className="field">
            <label htmlFor="bf-color">Color</label>
            <select id="bf-color" value={color} onChange={(e) => setColor(e.target.value)}>
              <option value="">— select —</option>
              {colors.map((c) => <option key={c} value={c}>{c}</option>)}
            </select>
          </div>
          <div className="field">
            <label htmlFor="bf-theme">Theme</label>
            <input id="bf-theme" value={theme} onChange={(e) => setTheme(e.target.value)} />
          </div>
          <div className="field">
            <label htmlFor="bf-aspect">Aspect</label>
            <select id="bf-aspect" value={aspect} onChange={(e) => setAspect(e.target.value)}>
              {ASPECTS.map((a) => <option key={a} value={a}>{a}</option>)}
            </select>
          </div>
        </div>

        <div className="field">
          <label htmlFor="bf-comment">Edit notes</label>
          <textarea
            id="bf-comment"
            value={comment}
            onChange={(e) => setComment(e.target.value)}
            placeholder="What to fix… (optional)"
            rows={2}
          />
        </div>

        {item.unknown_product && (
          <p className="alert alert-info" role="note">
            Unknown product — approve disabled. Flag for edits moves the image to edit/ (record saved);
            flag for regeneration moves it to rejected/.
          </p>
        )}

        {msg && <p className="alert alert-error" role="alert">{msg}</p>}
      </div>

      <div className="modal-foot">
        <div className="modal-foot-group">
          {status === "skipped" ? (
            <button disabled={busy} onClick={doUnskip}>Unskip</button>
          ) : (
            <button disabled={busy} onClick={doSkip}>Skip</button>
          )}
        </div>
        <div className="modal-foot-group">
          <button className="btn-danger" disabled={busy} onClick={doFlag}>Flag for regeneration</button>
          <button disabled={busy} onClick={doFlagEdit}>Flag for edits</button>
          <button className="btn-primary" disabled={busy || item.unknown_product || !color} onClick={doApprove}>
            Approve &amp; publish
          </button>
        </div>
      </div>

      {lightbox.node}
    </Modal>
  );
}
