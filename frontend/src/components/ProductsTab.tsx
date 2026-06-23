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
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => { getCategories().then(setCats).catch((e) => setErr(e.message)); }, []);

  const search = () => {
    setErr(null);
    const params: Parameters<typeof listProducts>[0] = { pending };
    if (category) params.category = category;
    if (idSingle && idEnd) { params.id_start = idSingle; params.id_end = idEnd; }
    else if (idSingle) params.id = idSingle;
    listProducts(params).then(setRows).catch((e) => setErr(e.message));
  };

  return (
    <div style={{ display: "flex", gap: 24 }}>
      <div style={{ flex: 1 }}>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
          <select value={category} onChange={(e) => setCategory(e.target.value)}>
            <option value="">All categories</option>
            {cats.map((c) => <option key={c.categoryid} value={c.categoryid}>{c.name}</option>)}
          </select>
          <input placeholder="Product ID (e.g. BC25001)" value={idSingle}
                 onChange={(e) => setIdSingle(e.target.value)} />
          <input placeholder="…to ID (range end, optional)" value={idEnd}
                 onChange={(e) => setIdEnd(e.target.value)} />
          <label><input type="checkbox" checked={pending}
                 onChange={(e) => setPending(e.target.checked)} /> pending only</label>
          <button onClick={search}>Search</button>
        </div>
        {err && <p style={{ color: "#b00" }}>{err}</p>}
        <table style={{ width: "100%", marginTop: 12, borderCollapse: "collapse" }}>
          <thead><tr><th align="left">ID</th><th align="left">Name</th><th align="left">Category</th><th>Status</th></tr></thead>
          <tbody>
            {rows.map((p) => (
              <tr key={p.productid} onClick={() => setSelected(p)}
                  style={{ cursor: "pointer", background: selected?.productid === p.productid ? "#eef" : undefined }}>
                <td>{p.productid}</td><td>{p.name}</td><td>{p.category_name ?? p.categoryid}</td>
                <td align="center">{p.base_mockup ? "✅ done" : "⏳ pending"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {selected && <ProductDetail product={selected} />}
    </div>
  );
}

function ProductDetail({ product }: { product: Product }) {
  const [prompts, setPrompts] = useState<Prompt[]>([]);
  const [promptText, setPromptText] = useState("");
  const [videoPrompt, setVideoPrompt] = useState("");
  const [msg, setMsg] = useState<string | null>(null);

  useEffect(() => {
    setMsg(null);
    if (!product.categoryid) { setPrompts([]); setPromptText(""); return; }
    listPrompts(product.categoryid).then((ps) => {
      setPrompts(ps);
      const def = ps.find((p) => p.is_default) ?? ps[0];
      setPromptText(def?.body ?? "");
    }).catch((e) => setMsg(e.message));
  }, [product.productid, product.categoryid]);

  const genImage = () =>
    generateImage({ productid: product.productid, prompt: promptText })
      .then((r) => setMsg(r.detail)).catch((e) => setMsg(e.message));
  const genVideo = () =>
    generateVideo({ productid: product.productid, prompt: videoPrompt })
      .then((r) => setMsg(r.detail)).catch((e) => setMsg(e.message));

  return (
    <div style={{ flex: 1, borderLeft: "1px solid #ddd", paddingLeft: 16 }}>
      <h3>{product.productid} — {product.name}</h3>
      {product.producturl
        ? <a href={product.producturl} target="_blank" rel="noreferrer">Open Drive folder ↗</a>
        : <em>No producturl</em>}
      <h4>Image prompt</h4>
      <select onChange={(e) => {
        const p = prompts.find((x) => String(x.prompt_id) === e.target.value);
        if (p) setPromptText(p.body);
      }}>
        {prompts.map((p) => <option key={p.prompt_id} value={p.prompt_id}>{p.label}{p.is_default ? " (default)" : ""}</option>)}
      </select>
      <textarea value={promptText} onChange={(e) => setPromptText(e.target.value)}
                rows={6} style={{ width: "100%", marginTop: 8 }} />
      <button onClick={genImage}>Generate Image</button>
      <h4>Video (custom prompt)</h4>
      <textarea value={videoPrompt} onChange={(e) => setVideoPrompt(e.target.value)}
                rows={4} style={{ width: "100%" }} placeholder="Describe the video for this product…" />
      <button onClick={genVideo} disabled={!videoPrompt.trim()}>Generate Video</button>
      {msg && <p style={{ marginTop: 8, color: "#555" }}>{msg}</p>}
    </div>
  );
}
