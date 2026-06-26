import { useCallback, useEffect, useRef, useState } from "react";
import {
  listBackfill, getBackfillCounts, rescanBackfill, getBackfillSources,
  approveBackfill, flagBackfill, flagEditBackfill, skipBackfill, unskipBackfill,
  type BackfillItem, type BackfillSources, type BackfillStatus, ApiError,
} from "../api";

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

type Dest = "approved" | "edit" | "regenerate";

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

  const loadCounts = useCallback(() => {
    getBackfillCounts().then((r) => setCounts(r.counts)).catch(() => {});
  }, []);

  const load = useCallback((s: BackfillStatus, off: number) => {
    setLoading(true);
    setError(null);
    listBackfill({ status: s, offset: off, limit: PAGE })
      .then((r) => {
        setItems(r.items);
        setTotal(r.total);
        setOffset(r.offset);
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
                <Card key={it.file_id} item={it}>
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
        <ReviewPanel item={active} onClose={() => setActive(null)} onResolved={onResolved} />
      )}
    </div>
  );
}

function Card({ item, children }: { item: BackfillItem; children: React.ReactNode }) {
  return (
    <div className="card bf-card stack-sm">
      {item.thumbnail_url ? (
        <img
          src={item.thumbnail_url}
          alt={`Generated mockup ${item.productid ?? item.filename}`}
          loading="lazy"
          style={{ width: "100%", borderRadius: "var(--r-md)", aspectRatio: "3 / 4", objectFit: "cover" }}
        />
      ) : (
        <div className="muted" style={{ aspectRatio: "3 / 4", display: "grid", placeItems: "center" }}>
          no preview
        </div>
      )}
      <div className="toolbar" style={{ justifyContent: "space-between", alignItems: "center" }}>
        <strong>{item.productid ?? item.filename}</strong>
        {item.unknown_product && <span className="pill pill-pending">unknown product</span>}
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
        className="modal stack"
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
  item, onClose, onResolved,
}: {
  item: BackfillItem;
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

  return (
    <Modal title={`Review ${item.productid ?? item.filename}`} onClose={onClose}>
      <div className="toolbar" style={{ justifyContent: "space-between", alignItems: "center" }}>
        <strong>{item.productid ?? item.filename}{item.product_name ? ` — ${item.product_name}` : ""}</strong>
        <button onClick={onClose}>Close</button>
      </div>

      <div className="split">
        <div className="stack-sm">
          <span className="section-label">Original images</span>
          {!data ? (
            <p className="muted">Loading…</p>
          ) : (
            <div style={gridStyle}>
              {[...data.originals.loose, ...data.originals.groups.flatMap((g) => g.images)].map((im) => (
                <img
                  key={im.id}
                  src={im.thumbnail_url}
                  alt={im.name}
                  loading="lazy"
                  style={{ width: "100%", borderRadius: "var(--r-sm)" }}
                />
              ))}
            </div>
          )}
        </div>
        <div className="stack-sm">
          <span className="section-label">Generated</span>
          <img
            src={data ? data.generated_preview : item.thumbnail_url ?? ""}
            alt={`Generated mockup ${item.productid ?? item.filename}`}
            style={{ width: "100%", borderRadius: "var(--r-sm)" }}
          />
        </div>
      </div>

      <div className="toolbar">
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

      {msg && <p className="alert" role="alert">{msg}</p>}

      <div className="toolbar">
        <button className="btn-primary" disabled={busy || item.unknown_product || !color} onClick={doApprove}>
          Approve &amp; publish
        </button>
        <button disabled={busy} onClick={doFlagEdit}>Flag for edits</button>
        <button className="btn-danger" disabled={busy} onClick={doFlag}>Flag for regeneration</button>
      </div>
      {item.unknown_product && (
        <p className="muted">
          Unknown product — approve disabled. Flag for edits moves the image to edit/ (record saved);
          flag for regeneration moves it to rejected/.
        </p>
      )}
    </Modal>
  );
}
