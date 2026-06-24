import { useEffect, useState } from "react";
import {
  getCategories, listPrompts, createPrompt, updatePrompt, deletePrompt,
  type Category, type Prompt,
} from "../api";
import RefineButton from "./RefineButton";
import { PlusIcon } from "./icons";

export default function PromptsTab() {
  const [cats, setCats] = useState<Category[]>([]);
  const [category, setCategory] = useState("");
  const [prompts, setPrompts] = useState<Prompt[]>([]);
  const [adding, setAdding] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => { getCategories().then(setCats).catch((e) => setErr(e.message)); }, []);

  const reload = (cat: string) => {
    if (!cat) { setPrompts([]); return; }
    listPrompts(cat).then(setPrompts).catch((e) => setErr(e.message));
  };
  useEffect(() => reload(category), [category]);

  const addNew = () => {
    setErr(null);
    setAdding(true);
    createPrompt({ categoryid: category, label: "New prompt", body: "" })
      .then(() => reload(category))
      .catch((e) => setErr(e.message))
      .finally(() => setAdding(false));
  };

  return (
    <div className="stack">
      {err && <p className="alert alert-error" role="alert">{err}</p>}

      <div className="toolbar">
        <div className="field">
          <label htmlFor="prm-cat">Category</label>
          <select id="prm-cat" value={category} onChange={(e) => setCategory(e.target.value)}>
            <option value="">Select category…</option>
            {cats.map((c) => <option key={c.categoryid} value={c.categoryid}>{c.name}</option>)}
          </select>
        </div>
        {category && (
          <button className="btn-primary" onClick={addNew} disabled={adding}>
            {adding ? <span className="spinner" aria-hidden /> : <PlusIcon />}
            {adding ? "Adding…" : "Add prompt"}
          </button>
        )}
      </div>

      {category && prompts.length === 0 && (
        <p className="empty">No prompts for this category yet — add one to get started.</p>
      )}
      {!category && (
        <p className="empty">Choose a category to view and edit its prompts.</p>
      )}

      <div className="stack">
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
  const [busy, setBusy] = useState<null | "save" | "delete">(null);

  const dirty =
    label !== prompt.label || body !== prompt.body || isDefault !== prompt.is_default;

  const save = () => {
    setBusy("save");
    updatePrompt(prompt.prompt_id, { label, body, is_default: isDefault })
      .then(onSaved)
      .catch((e) => onError(e.message))
      .finally(() => setBusy(null));
  };

  const remove = () => {
    if (!window.confirm(`Delete prompt "${prompt.label}"? This cannot be undone.`)) return;
    setBusy("delete");
    deletePrompt(prompt.prompt_id)
      .then(onDeleted)
      .catch((e) => onError(e.message))
      .finally(() => setBusy(null));
  };

  return (
    <div className="card stack-sm" style={{ padding: "var(--sp-4)" }}>
      <div className="toolbar" style={{ alignItems: "center" }}>
        <div className="field" style={{ flex: 1, minWidth: 180 }}>
          <label htmlFor={`lbl-${prompt.prompt_id}`}>Label</label>
          <input id={`lbl-${prompt.prompt_id}`} value={label}
                 onChange={(e) => setLabel(e.target.value)} placeholder="Label" />
        </div>
        <label className="check">
          <input type="checkbox" checked={isDefault}
                 onChange={(e) => setIsDefault(e.target.checked)} />
          Default
        </label>
        <button className="btn-primary" onClick={save} disabled={busy !== null || !dirty}>
          {busy === "save" && <span className="spinner" aria-hidden />}
          {busy === "save" ? "Saving…" : dirty ? "Save" : "Saved"}
        </button>
        <button className="btn-danger" onClick={remove} disabled={busy !== null}>
          {busy === "delete" && <span className="spinner" aria-hidden />}
          Delete
        </button>
      </div>
      <div className="field">
        <div className="toolbar" style={{ justifyContent: "space-between", alignItems: "center" }}>
          <label htmlFor={`body-${prompt.prompt_id}`}>Prompt body</label>
          <RefineButton
            kind="image"
            instruction={body}
            categoryid={prompt.categoryid}
            onRefined={setBody}
            onError={onError}
          />
        </div>
        <textarea id={`body-${prompt.prompt_id}`} value={body}
                  onChange={(e) => setBody(e.target.value)} rows={6} />
      </div>
    </div>
  );
}
