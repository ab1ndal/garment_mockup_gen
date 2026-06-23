import { useEffect, useState } from "react";
import {
  getCategories, listProducts, listPrompts, generateImage, generateVideo,
  type Category, type Product, type Prompt,
} from "../api";

export default function ProductsTab() {
  const [cats, setCats] = useState<Category[]>([]);
  const [category, setCategory] = useState("");
  const [idSingle, setIdSingle] = useState("");
  const [idEnd, setIdEnd] = useState("");
  const [pending, setPending] = useState(true);
  const [rows, setRows] = useState<Product[]>([]);
  const [selected, setSelected] = useState<Product | null>(null);
  const [searching, setSearching] = useState(false);
  const [searched, setSearched] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => { getCategories().then(setCats).catch((e) => setErr(e.message)); }, []);

  const search = () => {
    setErr(null);
    setSearching(true);
    const params: Parameters<typeof listProducts>[0] = { pending };
    if (category) params.category = category;
    if (idSingle && idEnd) { params.id_start = idSingle; params.id_end = idEnd; }
    else if (idSingle) params.id = idSingle;
    listProducts(params)
      .then((r) => { setRows(r); setSearched(true); })
      .catch((e) => setErr(e.message))
      .finally(() => setSearching(false));
  };

  return (
    <div className="split">
      <div>
        <form
          className="toolbar"
          onSubmit={(e) => { e.preventDefault(); search(); }}
        >
          <div className="field">
            <label htmlFor="flt-cat">Category</label>
            <select id="flt-cat" value={category} onChange={(e) => setCategory(e.target.value)}>
              <option value="">All categories</option>
              {cats.map((c) => <option key={c.categoryid} value={c.categoryid}>{c.name}</option>)}
            </select>
          </div>
          <div className="field">
            <label htmlFor="flt-id">Product ID</label>
            <input id="flt-id" placeholder="e.g. BC25001" value={idSingle}
                   onChange={(e) => setIdSingle(e.target.value)} />
          </div>
          <div className="field">
            <label htmlFor="flt-id-end">Range end</label>
            <input id="flt-id-end" placeholder="optional" value={idEnd}
                   onChange={(e) => setIdEnd(e.target.value)} />
          </div>
          <label className="check">
            <input type="checkbox" checked={pending}
                   onChange={(e) => setPending(e.target.checked)} />
            Pending only
          </label>
          <button type="submit" className="btn-primary" disabled={searching}>
            {searching && <span className="spinner" aria-hidden />}
            {searching ? "Searching…" : "Search"}
          </button>
        </form>

        {err && <p className="alert alert-error" role="alert" style={{ marginTop: "var(--sp-3)" }}>{err}</p>}

        {rows.length > 0 ? (
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th>ID</th><th>Name</th><th>Category</th><th>Status</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((p) => (
                  <tr key={p.productid} onClick={() => setSelected(p)}
                      aria-selected={selected?.productid === p.productid}>
                    <td className="mono">{p.productid}</td>
                    <td>{p.name}</td>
                    <td>{p.category_name ?? p.categoryid}</td>
                    <td>
                      <span className={p.base_mockup ? "pill pill-done" : "pill pill-pending"}>
                        {p.base_mockup ? "Done" : "Pending"}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="empty">
            {searched ? "No products match these filters." : "Run a search to list products."}
          </p>
        )}
      </div>

      {selected
        ? <ProductDetail product={selected} />
        : (
          <div className="detail card" style={{ padding: "var(--sp-5)" }}>
            <p className="empty" style={{ padding: "var(--sp-5) 0" }}>
              Select a product to generate its mockup.
            </p>
          </div>
        )}
    </div>
  );
}

function ProductDetail({ product }: { product: Product }) {
  const [prompts, setPrompts] = useState<Prompt[]>([]);
  const [promptText, setPromptText] = useState("");
  const [videoPrompt, setVideoPrompt] = useState("");
  const [busy, setBusy] = useState<null | "image" | "video">(null);
  const [msg, setMsg] = useState<{ kind: "info" | "error"; text: string } | null>(null);

  useEffect(() => {
    setMsg(null);
    if (!product.categoryid) { setPrompts([]); setPromptText(""); return; }
    listPrompts(product.categoryid).then((ps) => {
      setPrompts(ps);
      const def = ps.find((p) => p.is_default) ?? ps[0];
      setPromptText(def?.body ?? "");
    }).catch((e) => setMsg({ kind: "error", text: e.message }));
  }, [product.productid, product.categoryid]);

  const run = (kind: "image" | "video") => {
    setBusy(kind);
    setMsg(null);
    const call = kind === "image"
      ? generateImage({ productid: product.productid, prompt: promptText })
      : generateVideo({ productid: product.productid, prompt: videoPrompt });
    call
      .then((r) => setMsg({ kind: "info", text: r.detail }))
      .catch((e: Error) => setMsg({ kind: "error", text: e.message.replace(/^\d+:\s*/, "") }))
      .finally(() => setBusy(null));
  };

  return (
    <div className="detail card" style={{ padding: "var(--sp-5)" }}>
      <h3>
        <span className="mono">{product.productid}</span> — {product.name}
      </h3>
      <p style={{ margin: "var(--sp-2) 0 0" }}>
        {product.producturl
          ? <a href={product.producturl} target="_blank" rel="noreferrer">Open Drive folder ↗</a>
          : <span className="subtle">No Drive folder linked</span>}
      </p>

      <div className="field" style={{ marginTop: "var(--sp-5)" }}>
        <p className="section-label" style={{ margin: 0 }}>Image prompt</p>
        <select
          aria-label="Prompt template"
          onChange={(e) => {
            const p = prompts.find((x) => String(x.prompt_id) === e.target.value);
            if (p) setPromptText(p.body);
          }}
        >
          {prompts.length === 0 && <option>No templates for this category</option>}
          {prompts.map((p) => (
            <option key={p.prompt_id} value={p.prompt_id}>
              {p.label}{p.is_default ? " (default)" : ""}
            </option>
          ))}
        </select>
        <textarea
          aria-label="Image prompt text"
          value={promptText}
          onChange={(e) => setPromptText(e.target.value)}
          rows={6}
        />
        <button
          className="btn-primary"
          onClick={() => run("image")}
          disabled={busy !== null || !promptText.trim()}
        >
          {busy === "image" && <span className="spinner" aria-hidden />}
          {busy === "image" ? "Generating…" : "Generate Image"}
        </button>
      </div>

      <div className="field" style={{ marginTop: "var(--sp-5)" }}>
        <p className="section-label" style={{ margin: 0 }}>Video (custom prompt)</p>
        <textarea
          aria-label="Video prompt text"
          value={videoPrompt}
          onChange={(e) => setVideoPrompt(e.target.value)}
          rows={4}
          placeholder="Describe the video for this product…"
        />
        <button
          onClick={() => run("video")}
          disabled={busy !== null || !videoPrompt.trim()}
        >
          {busy === "video" && <span className="spinner" aria-hidden />}
          {busy === "video" ? "Generating…" : "Generate Video"}
        </button>
      </div>

      {msg && (
        <p
          className={msg.kind === "error" ? "alert alert-error" : "alert alert-info"}
          role={msg.kind === "error" ? "alert" : "status"}
          aria-live="polite"
          style={{ marginTop: "var(--sp-4)" }}
        >
          {msg.text}
        </p>
      )}
    </div>
  );
}
