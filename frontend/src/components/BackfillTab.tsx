import { useEffect, useRef, useState } from "react";
import {
  listBackfill, getBackfillSources, approveBackfill, flagBackfill,
  type BackfillItem, type BackfillSources, ApiError,
} from "../api";

const PAGE = 20;
const ASPECTS = ["1:1", "4:5", "3:4", "9:16", "16:9"];

const gridStyle: React.CSSProperties = {
  display: "grid",
  gridTemplateColumns: "repeat(auto-fill, minmax(200px, 1fr))",
  gap: "var(--sp-4)",
};

export default function BackfillTab() {
  const [items, setItems] = useState<BackfillItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [active, setActive] = useState<BackfillItem | null>(null);

  const load = (refresh = false) => {
    setLoading(true);
    setError(null);
    listBackfill({ offset: 0, limit: PAGE, refresh })
      .then((r) => {
        setItems(r.items);
        setTotal(r.total);
      })
      .catch((e: ApiError) => setError(e.message))
      .finally(() => setLoading(false));
  };

  useEffect(() => load(), []);

  const onResolved = (fileId: string) => {
    setItems((xs) => xs.filter((i) => i.file_id !== fileId));
    setTotal((t) => Math.max(0, t - 1));
    setActive(null);
  };

  if (loading) return <p className="muted">Loading mockups…</p>;
  if (error)
    return (
      <div className="stack">
        <p className="alert alert-error" role="alert">{error}</p>
        <button onClick={() => load()}>Retry</button>
      </div>
    );

  return (
    <div className="stack">
      <div className="toolbar" style={{ justifyContent: "space-between", alignItems: "center" }}>
        <span className="muted">{total} remaining</span>
        <button onClick={() => load(true)}>Refresh</button>
      </div>

      {items.length === 0 ? (
        <p className="muted">Nothing left to review.</p>
      ) : (
        <div style={gridStyle}>
          {items.map((it) => (
            <div key={it.file_id} className="card bf-card stack-sm">
              {it.thumbnail_url ? (
                <img
                  src={it.thumbnail_url}
                  alt={`Generated mockup ${it.productid ?? it.filename}`}
                  loading="lazy"
                  style={{ width: "100%", borderRadius: "var(--r-md)", aspectRatio: "3 / 4", objectFit: "cover" }}
                />
              ) : (
                <div className="muted" style={{ aspectRatio: "3 / 4", display: "grid", placeItems: "center" }}>
                  no preview
                </div>
              )}
              <div className="toolbar" style={{ justifyContent: "space-between", alignItems: "center" }}>
                <strong>{it.productid ?? it.filename}</strong>
                {it.unknown_product && <span className="pill pill-pending">unknown product</span>}
              </div>
              <button className="btn-primary" onClick={() => setActive(it)}>Review</button>
            </div>
          ))}
        </div>
      )}

      {active && (
        <ReviewPanel item={active} onClose={() => setActive(null)} onResolved={onResolved} />
      )}
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
  onResolved: (fileId: string) => void;
}) {
  const [data, setData] = useState<BackfillSources | null>(null);
  const [color, setColor] = useState<string>(item.colors.length === 1 ? item.colors[0] : "");
  const [theme, setTheme] = useState("Default");
  const [aspect, setAspect] = useState("1:1");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  useEffect(() => {
    getBackfillSources(item.file_id, item.productid)
      .then((d) => {
        setData(d);
        if (d.suggested_aspect) setAspect(d.suggested_aspect);
      })
      .catch((e: ApiError) => setMsg(e.message));
  }, [item.file_id, item.productid]);

  const doApprove = () => {
    if (!item.productid) return;
    setBusy(true);
    setMsg(null);
    approveBackfill({
      file_id: item.file_id, productid: item.productid,
      color: color || undefined, theme_name: theme, aspect_ratio: aspect,
    })
      .then((r) => {
        if (r.warning) setMsg(r.warning);
        onResolved(item.file_id);
      })
      .catch((e: ApiError) => setMsg(e.message))
      .finally(() => setBusy(false));
  };

  const doFlag = () => {
    setBusy(true);
    setMsg(null);
    flagBackfill({ file_id: item.file_id, productid: item.productid })
      .then(() => onResolved(item.file_id))
      .catch((e: ApiError) => setMsg(e.message))
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
            {item.colors.map((c) => <option key={c} value={c}>{c}</option>)}
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

      {msg && <p className="alert" role="alert">{msg}</p>}

      <div className="toolbar">
        <button className="btn-primary" disabled={busy || item.unknown_product || !color} onClick={doApprove}>
          Approve &amp; publish
        </button>
        <button className="btn-danger" disabled={busy} onClick={doFlag}>Flag for regeneration</button>
      </div>
      {item.unknown_product && (
        <p className="muted">Unknown product — approve disabled; flag will move the image to rejected/.</p>
      )}
    </Modal>
  );
}
