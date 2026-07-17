import { useCallback, useEffect, useRef, useState } from "react";
import {
  ApiError, getCategories, getGenerationOptions,
  enqueueBatch, listBatchItems, getBatchCounts, getBatchSources, getBatchCategorySummary,
  acceptBatch, editBatch, rejectBatch, retryBatch,
  type BatchItem, type BatchTabId, type BatchSources, type Category, type GenOptions,
  type BatchCategorySummary,
} from "../api";
import { useImageLightbox } from "./Lightbox";

const PAGE = 20;
const POLL_MS = 5000;

const RES_LABEL: Record<string, string> = {
  "512px": "0.5K", "1K": "1K", "2K": "2K · web", "4K": "4K · print",
};
const ASPECT_LABEL: Record<string, string> = {
  "1:1": "1:1 · square", "3:4": "3:4 · portrait", "4:3": "4:3 · landscape",
};

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

// A stable fingerprint of the per-status counts. Only the background worker
// mutates cards while polling, and every status change shifts a count, so an
// unchanged fingerprint means nothing on any page has changed.
function countsSig(counts: Record<string, number>): string {
  return Object.keys(counts).sort().map((k) => `${k}:${counts[k]}`).join(",");
}

export default function BatchTab() {
  const [cats, setCats] = useState<Category[]>([]);
  const [category, setCategory] = useState<string>("");
  const [count, setCount] = useState<number>(10);
  const [opts, setOpts] = useState<GenOptions | null>(null);
  const [model, setModel] = useState("");
  const [resolution, setResolution] = useState("");
  const [aspect, setAspect] = useState("");
  const [enqueuing, setEnqueuing] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const [tab, setTab] = useState<BatchTabId>("ready");
  const [items, setItems] = useState<BatchItem[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [counts, setCounts] = useState<Record<string, number>>({});
  const [summary, setSummary] = useState<BatchCategorySummary[]>([]);
  const [categoryFilter, setCategoryFilter] = useState<string | null>(null);
  const [search, setSearch] = useState("");            // raw input
  const [searchApplied, setSearchApplied] = useState(""); // debounced, drives the query
  const [loading, setLoading] = useState(false);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [review, setReview] = useState<BatchItem | null>(null);
  // Draft page-number for the jump-to-page input; synced to the live page below
  // so external navigation (Prev/Next/tab change/poll) keeps the field accurate.
  const [pageInput, setPageInput] = useState("1");

  // The page the reviewer intends to view. Updated synchronously on navigation
  // so the background poll always refetches the current page and a late response
  // for a page we've since left can be dropped.
  const offsetRef = useRef(0);
  // Active category filter, mirrored to a ref so the background poll's load
  // closure always filters by the category currently on screen.
  const categoryFilterRef = useRef<string | null>(null);
  // Active (debounced) product-id search, mirrored to a ref for the poll.
  const searchRef = useRef<string>("");
  // Last counts fingerprint the page was rendered against. The poll compares
  // against this to decide whether a refetch is actually needed.
  const lastCountsSigRef = useRef("");

  const lightbox = useImageLightbox();

  useEffect(() => { getCategories().then(setCats).catch(() => {}); }, []);

  useEffect(() => {
    getGenerationOptions().then((o) => {
      setOpts(o);
      setModel(o.defaults.model);
      setResolution(o.defaults.resolution);
      setAspect(o.defaults.aspect_ratio);
    }).catch(() => {});
  }, []);

  const loadCounts = useCallback(() => {
    getBatchCounts().then((r) => {
      setCounts(r.counts);
      lastCountsSigRef.current = countsSig(r.counts);
    }).catch(() => {});
  }, []);

  const loadSummary = useCallback(() => {
    getBatchCategorySummary().then((r) => setSummary(r.categories)).catch(() => {});
  }, []);

  // `quiet` swaps the page contents in place, without the loading state — a
  // background poll must not blank the grid the reviewer is looking at.
  const load = useCallback((t: BatchTabId, off: number, quiet = false) => {
    if (!quiet) { offsetRef.current = off; setLoading(true); setError(null); }
    listBatchItems({ tab: t, offset: off, limit: PAGE,
                     categoryid: categoryFilterRef.current, productid: searchRef.current || null })
      .then((r) => {
        // Drop a response for a page the reviewer has since navigated away from,
        // so a slow poll can't clobber a fresh page change.
        if (r.offset !== offsetRef.current) return;
        // Staged thumbnails are signed per read, so each poll returns a fresh URL
        // for the same image. Reuse the URL a card already rendered with — the
        // bytes never change once staged — so the browser doesn't re-download
        // every thumbnail on each poll.
        setItems((prev) => {
          const seen = new Map(prev.map((x) => [x.id, x.generated_thumb_url]));
          return r.items.map((it) => {
            const kept = seen.get(it.id);
            return kept ? { ...it, generated_thumb_url: kept } : it;
          });
        });
        setTotal(r.total); setOffset(r.offset);
      })
      .catch((e) => { if (!quiet) setError(e instanceof ApiError ? e.message : "Failed to load."); })
      .finally(() => { if (!quiet) setLoading(false); });
  }, []);

  // Keep the jump-to-page field showing the current page whenever the offset
  // moves for any reason other than the user typing in it.
  useEffect(() => { setPageInput(String(Math.floor(offset / PAGE) + 1)); }, [offset]);

  // Debounce the raw search box into the applied term (~300ms) so a burst of
  // keystrokes issues one query, not one per character.
  useEffect(() => {
    const t = setTimeout(() => setSearchApplied(search.trim()), 300);
    return () => clearTimeout(t);
  }, [search]);

  // Items are fetched 20 at a time on tab/category/search/page change, manual
  // refresh, or after an action. Changing tab, category, or search resets to the
  // first page.
  useEffect(() => {
    categoryFilterRef.current = categoryFilter;
    searchRef.current = searchApplied;
    load(tab, 0); loadCounts(); loadSummary();
  }, [tab, categoryFilter, searchApplied, load, loadCounts, loadSummary]);

  // Refresh in place: reuse the quiet path so the grid is swapped without a
  // loading teardown. Tearing the grid down unmounts every card and forces the
  // browser to re-request every thumbnail; the quiet path keeps the DOM and
  // reuses each card's existing signed URL, so unchanged images never reload.
  const refresh = () => { load(tab, offset, true); loadCounts(); };

  // Cards move queued -> generating -> ready on a background worker, so a static
  // page goes stale on its own. Poll while anything is still in flight, and stop
  // once the queue drains. Paused while a card is open for review or mid-action:
  // a reload must not swap the card out from under the reviewer.
  const inFlight = countFor("in_progress", counts);
  useEffect(() => {
    if (inFlight === 0 || review || busyId !== null) return;
    const t = setInterval(() => {
      // Poll the cheap counts endpoint, not the page. Refetch the page (and pay
      // for the row read + per-thumbnail URL signing) only when a status count
      // has actually moved; an unchanged fingerprint means the page is current.
      getBatchCounts().then((r) => {
        const sig = countsSig(r.counts);
        if (sig === lastCountsSigRef.current) return;
        lastCountsSigRef.current = sig;
        setCounts(r.counts);
        load(tab, offsetRef.current, true);
        loadSummary();
      }).catch(() => {});
    }, POLL_MS);
    return () => clearInterval(t);
  }, [inFlight, review, busyId, tab, load, loadSummary]);

  async function onEnqueue() {
    setEnqueuing(true); setNotice(null); setError(null);
    try {
      const r = await enqueueBatch({
        category: category || null, count,
        model: model || undefined,
        resolution: resolution || undefined,
        aspect_ratio: aspect || undefined,
      });
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
    loadSummary();
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
  const pages = Math.max(1, Math.ceil(total / PAGE));
  const currentPage = Math.floor(offset / PAGE) + 1;
  // Clamp to a valid page and navigate; always resync the input so a clamped or
  // invalid entry snaps back to a real page even when no navigation happens.
  const goToPage = (p: number) => {
    const clamped = Math.min(pages, Math.max(1, p >= 1 ? p : currentPage));
    setPageInput(String(clamped));
    if (!loading && clamped !== currentPage) load(tab, (clamped - 1) * PAGE);
  };

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
        {opts && (
          <>
            <div className="field">
              <label htmlFor="batch-model">Model</label>
              <select id="batch-model" value={model} onChange={(e) => setModel(e.target.value)}>
                {opts.models.map((m) => <option key={m} value={m}>{m}</option>)}
              </select>
            </div>
            <div className="field">
              <label htmlFor="batch-resolution">Quality</label>
              <select id="batch-resolution" value={resolution} onChange={(e) => setResolution(e.target.value)}>
                {opts.resolutions.map((r) => <option key={r} value={r}>{RES_LABEL[r] ?? r}</option>)}
              </select>
            </div>
            <div className="field">
              <label htmlFor="batch-aspect">Aspect ratio</label>
              <select id="batch-aspect" value={aspect} onChange={(e) => setAspect(e.target.value)}>
                {opts.aspect_ratios.map((a) => <option key={a} value={a}>{ASPECT_LABEL[a] ?? a}</option>)}
              </select>
            </div>
          </>
        )}
        <button className="btn-primary" onClick={onEnqueue} disabled={enqueuing}>
          {enqueuing ? "Queuing…" : "Generate"}
        </button>
        <button onClick={refresh} disabled={loading}>Refresh</button>
      </div>

      {notice && <p className="alert" role="status">{notice}</p>}
      {error && <p className="alert alert-error" role="alert">{error}</p>}

      {summary.length > 0 && (
        <details className="cat-review">
          <summary>
            Review by category
            {categoryFilter && (
              <span className="cat-active-badge">
                {summary.find((c) => c.categoryid === categoryFilter)?.name ?? categoryFilter}
              </span>
            )}
          </summary>
          <div className="cat-list" role="listbox" aria-label="Filter review queue by category">
            <button type="button" className={`cat-row${categoryFilter === null ? " active" : ""}`}
                    aria-pressed={categoryFilter === null} onClick={() => setCategoryFilter(null)}>
              <span className="cat-name">All categories</span>
            </button>
            {summary.map((c) => (
              <button key={c.categoryid} type="button"
                      className={`cat-row${categoryFilter === c.categoryid ? " active" : ""}`}
                      aria-pressed={categoryFilter === c.categoryid}
                      onClick={() => setCategoryFilter(categoryFilter === c.categoryid ? null : c.categoryid)}>
                <span className="cat-name">{c.name ?? c.categoryid}</span>
                <span className="cat-counts">
                  <span className="cat-chip chip-unpub" title="Unpublished products — no mockup yet">{c.unpublished}</span>
                  <span className="cat-chip chip-ready" title="Ready to review">{c.ready}</span>
                  <span className="cat-chip chip-queued" title="Queued or generating">{c.queued}</span>
                </span>
              </button>
            ))}
          </div>
          <p className="muted cat-legend">
            Per category: <b>unpublished</b> products · <b>ready</b> to review · <b>queued</b> or generating
          </p>
        </details>
      )}

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
        <div className="toolbar" style={{ justifyContent: "space-between", alignItems: "center", gap: "var(--sp-3)" }}>
          <span className="muted">
            {loading ? "Loading…" : total === 0 ? "0 items" : `${start}–${end} of ${total}`}
          </span>
          <div style={{ position: "relative", display: "inline-flex", alignItems: "center" }}>
            <input type="search" inputMode="text" value={search}
                   aria-label="Search cards by product id"
                   placeholder="Search product id…"
                   onChange={(e) => setSearch(e.target.value)}
                   style={{ width: "min(15rem, 60vw)", paddingRight: search ? "2rem" : undefined }} />
            {search && (
              <button type="button" aria-label="Clear search"
                      onClick={() => setSearch("")}
                      style={{ position: "absolute", right: 4, minHeight: "auto", height: 28, width: 28,
                               padding: 0, border: "none", background: "transparent" }}>
                ✕
              </button>
            )}
          </div>
        </div>

        {loading ? (
          <p className="muted">Loading cards…</p>
        ) : items.length === 0 ? (
          <p className="muted">
            {searchApplied ? `No cards match "${searchApplied}" in ${TABS.find((t) => t.id === tab)?.label}.` : EMPTY_COPY[tab]}
          </p>
        ) : (
          <>
            {tab === "history" ? <HistoryTable items={items} /> : (
              <div style={gridStyle}>
                {items.map((it) => (
                  <Card key={it.id} item={it}
                        onEnlarge={() => it.generated_thumb_url
                          && lightbox.show(it.generated_thumb_url, it.productid)}>
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
                  </Card>
                ))}
              </div>
            )}

            {total > PAGE && (
              <div className="toolbar" style={{ justifyContent: "center", alignItems: "center", gap: "var(--sp-2)", flexWrap: "wrap" }}>
                <button aria-label="First page" disabled={offset === 0 || loading} onClick={() => goToPage(1)}>
                  « First
                </button>
                <button aria-label="Previous page" disabled={offset === 0 || loading} onClick={() => goToPage(currentPage - 1)}>
                  ‹ Prev
                </button>
                <span className="muted" style={{ display: "inline-flex", alignItems: "center", gap: "var(--sp-2)" }}>
                  Page
                  <input
                    type="text"
                    inputMode="numeric"
                    aria-label={`Page number, ${pages} total. Enter a page and press Enter to jump`}
                    value={pageInput}
                    disabled={loading}
                    onChange={(e) => setPageInput(e.target.value.replace(/[^0-9]/g, ""))}
                    onKeyDown={(e) => { if (e.key === "Enter") goToPage(Number(pageInput)); }}
                    onBlur={() => goToPage(Number(pageInput))}
                    style={{ width: "3.25rem", textAlign: "center", padding: "6px 8px" }}
                  />
                  of {pages}
                </span>
                <button aria-label="Next page" disabled={end >= total || loading} onClick={() => goToPage(currentPage + 1)}>
                  Next ›
                </button>
                <button aria-label="Last page" disabled={end >= total || loading} onClick={() => goToPage(pages)}>
                  Last »
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

// Handled cards keep no image — accept and reject both discard the staged file —
// so History is a plain record of what happened to each card, not a thumbnail grid.
function HistoryTable({ items }: { items: BatchItem[] }) {
  return (
    <div className="table-wrap">
      <table className="data is-static">
        <thead>
          <tr>
            <th scope="col">Product ID</th>
            <th scope="col">Color</th>
            <th scope="col">Status</th>
          </tr>
        </thead>
        <tbody>
          {items.map((it) => (
            <tr key={it.id}>
              <td className="mono">{it.productid}</td>
              <td>{it.color || <span className="muted">—</span>}</td>
              <td>
                <span className={`pill ${it.status === "published" ? "pill-done" : ""}`}>
                  {it.status}
                </span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
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
                            onClick={() => lightbox.showDrive(s.id, `Source ${s.id}`, s.thumb_url)}
                            aria-label={`Enlarge source ${s.id}`}>
                      <img src={s.thumb_url} alt="source" loading="lazy" />
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
            {item.generated_thumb_url ? (
              <button type="button" className="img-frame img-zoom"
                      onClick={() => lightbox.show(item.generated_thumb_url!, "generated mockup")}
                      aria-label="Enlarge generated mockup">
                <img src={item.generated_thumb_url} alt="generated mockup" />
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
