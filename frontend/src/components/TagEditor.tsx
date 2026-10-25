import type { ProposedTag, TagGroup, TagPick, TagRow } from "../api";

const CheckIcon = () => (
  <svg className="tag-check" viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
    <path d="M3 8.5l3 3 7-7" fill="none" stroke="currentColor" strokeWidth="2"
      strokeLinecap="round" strokeLinejoin="round" />
  </svg>
);

const labelOf = (groups: TagGroup[], group: string, value: string) =>
  groups.find((g) => g.group === group)?.values.find((v) => v.value === value)?.label ?? value;

/** The product's saved tags, read-only, grouped by tag group. */
export function CurrentTags({ groups, current }: { groups: TagGroup[]; current: TagRow[] }) {
  if (current.length === 0) return <p className="muted">No tags yet.</p>;
  const order = groups.map((g) => g.group);
  const byGroup = [...new Set(current.map((t) => t.group))]
    .sort((a, b) => order.indexOf(a) - order.indexOf(b));
  return (
    <dl className="tag-current">
      {byGroup.map((group) => (
        <div key={group} className="tag-current-row">
          <dt>{groups.find((g) => g.group === group)?.label ?? group}</dt>
          <dd>
            {current.filter((t) => t.group === group).map((t) => (
              <span key={t.value} className="tag-chip tag-chip-static">
                {labelOf(groups, group, t.value)}
                {t.source === "manual" && <span className="tag-mark">Manual</span>}
              </span>
            ))}
          </dd>
        </div>
      ))}
    </dl>
  );
}

/**
 * Editable tags under a description draft. AI proposals start selected; any
 * other allowed value can be added (saved as `manual`). Hand-entered tags
 * already on the product are kept by the save, so they show locked and are
 * never part of `picks`.
 */
export default function TagEditor({ groups, manual, proposed, picks, onChange }: {
  groups: TagGroup[];
  manual: TagRow[];
  proposed: ProposedTag[];
  picks: TagPick[];
  onChange: (p: TagPick[]) => void;
}) {
  const isManual = (g: string, v: string) => manual.some((t) => t.group === g && t.value === v);
  const isPicked = (g: string, v: string) => picks.some((t) => t.group === g && t.value === v);
  const proposalOf = (g: string, v: string) => proposed.find((t) => t.group === g && t.value === v);

  const pick = (g: string, v: string): TagPick => {
    const ai = proposalOf(g, v);
    return ai
      ? { group: g, value: v, source: "ai_reviewed", confidence: ai.confidence }
      : { group: g, value: v, source: "manual", confidence: null };
  };

  const toggle = (g: string, v: string) =>
    onChange(isPicked(g, v)
      ? picks.filter((t) => !(t.group === g && t.value === v))
      : [...picks, pick(g, v)]);

  const choose = (g: string, v: string | null) =>
    onChange([...picks.filter((t) => t.group !== g), ...(v ? [pick(g, v)] : [])]);

  return (
    <div className="stack-sm tag-editor">
      {groups.map((g) => {
        const lockedSingle = g.single && manual.some((t) => t.group === g.group);
        const current = picks.find((t) => t.group === g.group)?.value ?? null;
        return (
          <fieldset key={g.group} className="tag-group">
            <legend>{g.label}{g.single && <span className="muted"> · pick one</span>}</legend>
            {lockedSingle && <p className="muted tag-note">Hand-entered value kept.</p>}
            <div className="tag-chips" role={g.single ? "radiogroup" : "group"} aria-label={g.label}>
              {g.single && (
                <button type="button" className="tag-chip" role="radio" aria-checked={current === null && !lockedSingle}
                  disabled={lockedSingle} onClick={() => choose(g.group, null)}>
                  {current === null && !lockedSingle && <CheckIcon />}None
                </button>
              )}
              {g.values.map(({ value, label }) => {
                const locked = isManual(g.group, value);
                const on = locked || isPicked(g.group, value);
                const ai = proposalOf(g.group, value) !== undefined;
                const common = {
                  type: "button" as const,
                  className: on ? "tag-chip tag-chip-on" : "tag-chip",
                  disabled: locked || lockedSingle,
                  title: locked ? "Hand-entered; kept" : undefined,
                };
                const body = (
                  <>
                    {on && <CheckIcon />}{label}
                    {ai && <span className="tag-mark">AI</span>}
                    {locked && <span className="tag-mark">Manual</span>}
                  </>
                );
                return g.single ? (
                  <button key={value} {...common} role="radio" aria-checked={on}
                    onClick={() => choose(g.group, value)}>{body}</button>
                ) : (
                  <button key={value} {...common} aria-pressed={on}
                    onClick={() => toggle(g.group, value)}>{body}</button>
                );
              })}
            </div>
          </fieldset>
        );
      })}
    </div>
  );
}
