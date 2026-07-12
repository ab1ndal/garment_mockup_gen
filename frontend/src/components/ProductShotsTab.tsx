import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  ApiError,
  DEFAULT_EDIT_PARAMS,
  createEditPreset,
  deleteEditPreset,
  getCategories,
  getImportDriveImages,
  getProductColors,
  listEditPresets,
  listProducts,
  markEditPresetDefault,
  previewImportShot,
  publishImportShot,
  warmImportShot,
  type Category,
  type EditParams,
  type EditPreset,
  type ImportImage,
  type Product,
} from "../api";
import { CheckIcon, ImageIcon } from "./icons";

const PREVIEW_DEBOUNCE_MS = 450;
const gridStyle: React.CSSProperties = {
  display: "grid",
  gridTemplateColumns: "repeat(auto-fill, minmax(160px, 1fr))",
  gap: "var(--sp-3)",
};

function msg(e: unknown): string {
  const m = e instanceof ApiError ? e.message : String(e);
  return m.replace(/^\d+:\s*/, "");
}

/** Native range wrapped as a labelled field with a live value read-out. */
function Slider(props: {
  label: string;
  value: number;
  min: number;
  max: number;
  step: number;
  fmt?: (v: number) => string;
  onChange: (v: number) => void;
}) {
  const { label, value, min, max, step, fmt, onChange } = props;
  return (
    <label className="field mb-0!">
      <span className="flex items-center justify-between text-xs font-semibold text-subtle">
        <span>{label}</span>
        <span className="mono text-ink">{fmt ? fmt(value) : value}</span>
      </span>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        aria-label={label}
        onChange={(e) => onChange(Number(e.target.value))}
        style={{ accentColor: "var(--accent)" }}
      />
    </label>
  );
}

function Toggle(props: {
  label: string;
  checked: boolean;
  onChange: (v: boolean) => void;
}) {
  return (
    <label className="check" style={{ minHeight: 44 }}>
      <input
        type="checkbox"
        checked={props.checked}
        onChange={(e) => props.onChange(e.target.checked)}
      />
      <span>{props.label}</span>
    </label>
  );
}

export default function ProductShotsTab() {
  // --- product picker ---
  const [cats, setCats] = useState<Category[]>([]);
  const [category, setCategory] = useState("");
  const [idText, setIdText] = useState("");
  const [products, setProducts] = useState<Product[]>([]);
  const [picking, setPicking] = useState(false);
  const [selected, setSelected] = useState<Product | null>(null);

  // --- images + colours for the selected product ---
  const [images, setImages] = useState<ImportImage[]>([]);
  const [published, setPublished] = useState<Set<string>>(new Set());
  const [colors, setColors] = useState<string[]>([]);
  const [loadingImages, setLoadingImages] = useState(false);

  // --- editing one image ---
  const [active, setActive] = useState<ImportImage | null>(null);
  const [params, setParams] = useState<EditParams>(DEFAULT_EDIT_PARAMS);
  const [color, setColor] = useState("");
  const [preview, setPreview] = useState<string | null>(null);
  const [previewing, setPreviewing] = useState(false);
  const [publishing, setPublishing] = useState(false);

  // --- presets ---
  const [presets, setPresets] = useState<EditPreset[]>([]);

  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    getCategories().then(setCats).catch((e) => setError(msg(e)));
    listEditPresets()
      .then((r) => setPresets(r.presets))
      .catch(() => setPresets([]));
  }, []);

  const defaultPreset = useMemo(
    () => presets.find((p) => p.is_default) ?? null,
    [presets],
  );

  const search = useCallback(() => {
    setPicking(true);
    setError(null);
    listProducts({ category, id: idText || undefined, pending: false, limit: 40 })
      .then(setProducts)
      .catch((e) => setError(msg(e)))
      .finally(() => setPicking(false));
  }, [category, idText]);

  const pickProduct = useCallback(
    (p: Product) => {
      setSelected(p);
      setActive(null);
      setPreview(null);
      setImages([]);
      setPublished(new Set());
      setLoadingImages(true);
      setError(null);
      getImportDriveImages(p.productid)
        .then((r) => {
          const flat = [...r.loose, ...r.groups.flatMap((g) => g.images)];
          setImages(flat);
        })
        .catch((e) => setError(msg(e)))
        .finally(() => setLoadingImages(false));
      getProductColors(p.productid)
        .then((r) => setColors(r.colors))
        .catch(() => setColors([]));
    },
    [],
  );

  const openEditor = useCallback(
    (img: ImportImage) => {
      setActive(img);
      warmImportShot(img.id).catch(() => {}); // best-effort: preview still computes on miss
      setPreview(null);
      setColor("");
      // default preset auto-applies; otherwise pipeline defaults
      setParams(defaultPreset ? { ...defaultPreset.params } : DEFAULT_EDIT_PARAMS);
    },
    [defaultPreset],
  );

  // debounced live preview whenever the active image or params change
  const reqId = useRef(0);
  useEffect(() => {
    if (!active) return;
    const id = ++reqId.current;
    setPreviewing(true);
    const t = setTimeout(() => {
      previewImportShot(active.id, params)
        .then((r) => {
          if (id === reqId.current) setPreview(r.preview);
        })
        .catch((e) => {
          if (id === reqId.current) setError(msg(e));
        })
        .finally(() => {
          if (id === reqId.current) setPreviewing(false);
        });
    }, PREVIEW_DEBOUNCE_MS);
    return () => clearTimeout(t);
  }, [active, params]);

  const set = <K extends keyof EditParams>(k: K, v: EditParams[K]) =>
    setParams((p) => ({ ...p, [k]: v }));

  const doPublish = () => {
    if (!selected || !active) return;
    setPublishing(true);
    setError(null);
    setNotice(null);
    publishImportShot({
      productid: selected.productid,
      file_id: active.id,
      color: color || null,
      params,
    })
      .then((r) => {
        setPublished((s) => new Set(s).add(active.id));
        setNotice(`Published as image #${r.displayorder}.`);
        setActive(null);
        setPreview(null);
      })
      .catch((e) => setError(msg(e)))
      .finally(() => setPublishing(false));
  };

  const saveAsPreset = () => {
    const name = window.prompt("Preset name")?.trim();
    if (!name) return;
    const makeDefault = window.confirm("Make this the default preset?");
    createEditPreset({ name, params, is_default: makeDefault })
      .then(() => listEditPresets())
      .then((r) => {
        setPresets(r.presets);
        setNotice(`Preset "${name}" saved.`);
      })
      .catch((e) => setError(msg(e)));
  };

  const applyPreset = (id: string) => {
    const p = presets.find((x) => String(x.preset_id) === id);
    if (p) setParams({ ...p.params });
  };

  const removePreset = (id: number) => {
    if (!window.confirm("Delete this preset?")) return;
    deleteEditPreset(id)
      .then(() => listEditPresets())
      .then((r) => setPresets(r.presets))
      .catch((e) => setError(msg(e)));
  };

  const makeDefault = (id: number) => {
    markEditPresetDefault(id)
      .then(() => listEditPresets())
      .then((r) => setPresets(r.presets))
      .catch((e) => setError(msg(e)));
  };

  return (
    <div className="grid items-start gap-6 lg:grid-cols-[minmax(280px,340px)_1fr]">
      {/* ---- left: product picker ---- */}
      <aside className="card stack-sm p-4">
        <h2 className="section-label">Product</h2>
        <div className="field">
          <label htmlFor="ps-cat">Category</label>
          <select
            id="ps-cat"
            value={category}
            onChange={(e) => setCategory(e.target.value)}
          >
            <option value="">All categories</option>
            {cats.map((c) => (
              <option key={c.categoryid} value={c.categoryid}>
                {c.name}
              </option>
            ))}
          </select>
        </div>
        <div className="field">
          <label htmlFor="ps-id">Product ID</label>
          <input
            id="ps-id"
            value={idText}
            placeholder="optional"
            onChange={(e) => setIdText(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && search()}
          />
        </div>
        <button className="btn-primary" onClick={search} disabled={picking}>
          {picking && <span className="spinner" aria-hidden />} Search
        </button>

        <ul className="stack-sm mt-2 max-h-[52vh] overflow-auto" role="list">
          {products.map((p) => {
            const sel = selected?.productid === p.productid;
            return (
              <li key={p.productid}>
                <button
                  type="button"
                  onClick={() => pickProduct(p)}
                  aria-current={sel}
                  className={`flex w-full items-center gap-3 border-l-2 px-3 py-2 text-left ${
                    sel
                      ? "border-l-accent bg-accent-soft"
                      : "border-l-transparent hover:bg-surface-2"
                  }`}
                >
                  <span className="min-w-0 flex-1">
                    <span className="block truncate font-medium text-ink">
                      {p.name}
                    </span>
                    <span className="mono block text-xs text-subtle">
                      {p.productid}
                    </span>
                  </span>
                </button>
              </li>
            );
          })}
          {!picking && products.length === 0 && (
            <li className="text-sm text-subtle">No products — search above.</li>
          )}
        </ul>
      </aside>

      {/* ---- right: images + editor ---- */}
      <section className="stack">
        {error && (
          <p className="alert alert-error" role="alert">
            {error}
          </p>
        )}
        {notice && (
          <p className="alert alert-info" role="status" aria-live="polite">
            {notice}
          </p>
        )}

        {!selected && (
          <div className="card flex min-h-[50vh] flex-col items-center justify-center gap-3 p-8 text-center">
            <ImageIcon />
            <h2 className="font-display text-2xl text-ink">Import product shots</h2>
            <p className="max-w-sm text-muted">
              Pick a product to load its Drive photos, clean them up, and publish
              — no AI generation, published after the model mockups.
            </p>
          </div>
        )}

        {selected && !active && (
          <div className="card stack-sm p-4">
            <div className="toolbar">
              <h2 className="section-label">
                Drive images · {selected.name}
              </h2>
              {loadingImages && <span className="spinner" aria-hidden />}
            </div>
            {!loadingImages && images.length === 0 && (
              <p className="empty">No images found in this product's Drive folder.</p>
            )}
            <div style={gridStyle}>
              {images.map((img) => {
                const done = published.has(img.id);
                return (
                  <button
                    key={img.id}
                    type="button"
                    className="card img-zoom stack-sm p-2 text-left"
                    onClick={() => openEditor(img)}
                    aria-label={`Edit ${img.name}`}
                  >
                    <div className="img-frame" style={{ aspectRatio: "1 / 1" }}>
                      {img.thumbnail_url ? (
                        <img
                          src={img.thumbnail_url}
                          alt={img.name}
                          loading="lazy"
                          style={{ width: "100%", height: "100%", objectFit: "cover" }}
                        />
                      ) : (
                        <span className="text-xs text-subtle">no preview</span>
                      )}
                    </div>
                    <span className="flex items-center justify-between gap-2">
                      <span className="mono truncate text-xs text-subtle">
                        {img.name}
                      </span>
                      {done && (
                        <span className="pill pill-done">
                          <CheckIcon size={12} /> done
                        </span>
                      )}
                    </span>
                  </button>
                );
              })}
            </div>
          </div>
        )}

        {selected && active && (
          <div className="grid items-start gap-4 xl:grid-cols-[1fr_320px]">
            {/* before / after */}
            <div className="card stack-sm p-4">
              <div className="toolbar">
                <div>
                  <h2 className="section-label">Preview · {active.name}</h2>
                  <span className="mono block text-xs text-subtle">
                    Product ID: {selected.productid}
                  </span>
                </div>
                <button
                  className="btn-ghost"
                  onClick={() => setActive(null)}
                  aria-label="Back to image list"
                >
                  ← Back
                </button>
              </div>
              <div className="grid gap-3 sm:grid-cols-2">
                <figure className="stack-sm">
                  <div className="img-frame" style={{ aspectRatio: "1 / 1" }}>
                    {active.thumbnail_url && (
                      <img
                        src={active.thumbnail_url}
                        alt={`Original ${active.name}`}
                        style={{ width: "100%", height: "100%", objectFit: "contain" }}
                      />
                    )}
                  </div>
                  <figcaption className="text-center text-xs text-subtle">
                    Original
                  </figcaption>
                </figure>
                <figure className="stack-sm">
                  <div
                    className="img-frame relative"
                    style={{
                      aspectRatio: "1 / 1",
                      background: params.bg === "cream" ? "#FAF7F0" : "#FFFFFF",
                    }}
                  >
                    {preview ? (
                      <img
                        src={preview}
                        alt={`Edited ${active.name}`}
                        style={{ width: "100%", height: "100%", objectFit: "contain" }}
                      />
                    ) : (
                      <span className="text-xs text-subtle">rendering…</span>
                    )}
                    {previewing && (
                      <span
                        className="spinner"
                        aria-hidden
                        style={{ position: "absolute", top: 8, right: 8 }}
                      />
                    )}
                  </div>
                  <figcaption className="text-center text-xs text-subtle">
                    Edited{previewing ? " · updating…" : ""}
                  </figcaption>
                </figure>
              </div>
            </div>

            {/* controls */}
            <div className="card stack-sm p-4">
              <h2 className="section-label">Adjust</h2>

              {presets.length > 0 && (
                <div className="field mb-0!">
                  <label htmlFor="ps-preset">Preset</label>
                  <select
                    id="ps-preset"
                    defaultValue=""
                    onChange={(e) => {
                      applyPreset(e.target.value);
                      e.target.value = "";
                    }}
                  >
                    <option value="" disabled>
                      Apply a preset…
                    </option>
                    {presets.map((p) => (
                      <option key={p.preset_id} value={p.preset_id}>
                        {p.name}
                        {p.is_default ? " (default)" : ""}
                      </option>
                    ))}
                  </select>
                </div>
              )}

              <div className="toolbar">
                <button
                  className="btn-ghost"
                  style={{ minHeight: 44 }}
                  onClick={() =>
                    set("rotate_quarter", ((params.rotate_quarter + 1) % 4) as EditParams["rotate_quarter"])
                  }
                >
                  ⟳ Rotate 90° ({params.rotate_quarter * 90}°)
                </button>
              </div>

              <Slider
                label="Straighten"
                value={params.straighten_deg}
                min={-15}
                max={15}
                step={0.5}
                fmt={(v) => `${v}°`}
                onChange={(v) => set("straighten_deg", v)}
              />
              <Slider
                label="Brightness"
                value={params.brightness}
                min={0.5}
                max={1.5}
                step={0.05}
                fmt={(v) => v.toFixed(2)}
                onChange={(v) => set("brightness", v)}
              />
              <Slider
                label="Saturation"
                value={params.saturation}
                min={0.5}
                max={1.5}
                step={0.05}
                fmt={(v) => v.toFixed(2)}
                onChange={(v) => set("saturation", v)}
              />

              <Toggle
                label="Auto-contrast"
                checked={params.autocontrast}
                onChange={(v) => set("autocontrast", v)}
              />
              <Toggle
                label="White balance"
                checked={params.white_balance}
                onChange={(v) => set("white_balance", v)}
              />
              <Toggle
                label="Drop shadow"
                checked={params.shadow}
                onChange={(v) => set("shadow", v)}
              />

              <fieldset className="field mb-0!">
                <legend className="text-xs font-semibold text-subtle">
                  Background
                </legend>
                <div className="toolbar" role="radiogroup" aria-label="Background colour">
                  {(["white", "cream"] as const).map((b) => (
                    <button
                      key={b}
                      type="button"
                      role="radio"
                      aria-checked={params.bg === b}
                      className={`pill ${params.bg === b ? "pill-done" : "pill-pending"}`}
                      style={{ minHeight: 44, textTransform: "capitalize" }}
                      onClick={() => set("bg", b)}
                    >
                      {b}
                    </button>
                  ))}
                </div>
              </fieldset>

              {colors.length > 0 && (
                <div className="field mb-0!">
                  <label htmlFor="ps-color">Variant colour</label>
                  <select
                    id="ps-color"
                    value={color}
                    onChange={(e) => setColor(e.target.value)}
                  >
                    <option value="">— no colour —</option>
                    {colors.map((c) => (
                      <option key={c} value={c}>
                        {c}
                      </option>
                    ))}
                  </select>
                </div>
              )}

              <div className="stack-sm mt-2">
                <button
                  className="btn-primary"
                  disabled={publishing || previewing || !preview}
                  onClick={doPublish}
                >
                  {publishing && <span className="spinner" aria-hidden />} Publish to
                  Supabase
                </button>
                <button className="btn-ghost" onClick={saveAsPreset}>
                  Save current as preset
                </button>
              </div>
            </div>
          </div>
        )}

        {/* preset management */}
        {selected && presets.length > 0 && (
          <div className="card stack-sm p-4">
            <h2 className="section-label">Presets</h2>
            <ul className="stack-sm" role="list">
              {presets.map((p) => (
                <li key={p.preset_id} className="toolbar justify-between">
                  <span className="flex items-center gap-2">
                    <strong className="text-ink">{p.name}</strong>
                    {p.is_default && <span className="pill pill-done">default</span>}
                  </span>
                  <span className="toolbar">
                    {!p.is_default && (
                      <button
                        className="btn-ghost"
                        onClick={() => makeDefault(p.preset_id)}
                      >
                        Make default
                      </button>
                    )}
                    <button
                      className="btn-danger"
                      onClick={() => removePreset(p.preset_id)}
                      aria-label={`Delete preset ${p.name}`}
                    >
                      Delete
                    </button>
                  </span>
                </li>
              ))}
            </ul>
          </div>
        )}
      </section>
    </div>
  );
}
