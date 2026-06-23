import { useEffect, useState } from "react";
import {
  getCategories, listPrompts, createPrompt, updatePrompt, deletePrompt,
  type Category, type Prompt,
} from "../api";

export default function PromptsTab() {
  const [cats, setCats] = useState<Category[]>([]);
  const [category, setCategory] = useState("");
  const [prompts, setPrompts] = useState<Prompt[]>([]);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => { getCategories().then(setCats).catch((e) => setErr(e.message)); }, []);

  const reload = (cat: string) => {
    if (!cat) { setPrompts([]); return; }
    listPrompts(cat).then(setPrompts).catch((e) => setErr(e.message));
  };
  useEffect(() => reload(category), [category]);

  const addNew = () =>
    createPrompt({ categoryid: category, label: "New prompt", body: "" })
      .then(() => reload(category)).catch((e) => setErr(e.message));

  return (
    <div>
      {err && <p style={{ color: "#b00" }}>{err}</p>}
      <select value={category} onChange={(e) => setCategory(e.target.value)}>
        <option value="">Select category…</option>
        {cats.map((c) => <option key={c.categoryid} value={c.categoryid}>{c.name}</option>)}
      </select>
      {category && <button onClick={addNew} style={{ marginLeft: 8 }}>+ Add prompt</button>}
      <div style={{ marginTop: 16, display: "flex", flexDirection: "column", gap: 16 }}>
        {prompts.map((p) => (
          <PromptEditor key={p.prompt_id} prompt={p}
            onSaved={() => reload(category)} onDeleted={() => reload(category)} onError={setErr} />
        ))}
      </div>
    </div>
  );
}

function PromptEditor({ prompt, onSaved, onDeleted, onError }: {
  prompt: Prompt; onSaved: () => void; onDeleted: () => void; onError: (m: string) => void;
}) {
  const [label, setLabel] = useState(prompt.label);
  const [body, setBody] = useState(prompt.body);
  const [isDefault, setIsDefault] = useState(prompt.is_default);

  const save = () =>
    updatePrompt(prompt.prompt_id, { label, body, is_default: isDefault })
      .then(onSaved).catch((e) => onError(e.message));
  const remove = () =>
    deletePrompt(prompt.prompt_id).then(onDeleted).catch((e) => onError(e.message));

  return (
    <div style={{ border: "1px solid #ddd", borderRadius: 6, padding: 12 }}>
      <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
        <input value={label} onChange={(e) => setLabel(e.target.value)} placeholder="Label" />
        <label><input type="checkbox" checked={isDefault}
               onChange={(e) => setIsDefault(e.target.checked)} /> default</label>
        <button onClick={save}>Save</button>
        <button onClick={remove} style={{ color: "#b00" }}>Delete</button>
      </div>
      <textarea value={body} onChange={(e) => setBody(e.target.value)} rows={6}
                style={{ width: "100%", marginTop: 8 }} />
    </div>
  );
}
