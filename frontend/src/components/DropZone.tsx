import { useId, useRef, useState } from "react";
import { ImageIcon, TrashIcon, UploadIcon } from "./icons";

type DropZoneProps = {
  /** Accessible label for the picker (also the native input's aria-label). */
  ariaLabel: string;
  /** Short format hint shown in the empty state, e.g. "PNG or JPG". */
  hint?: string;
  accept?: string;
  multiple?: boolean;
  disabled?: boolean;
  /** Called with the picked files (already filtered to the accept type on drop). */
  onFiles: (files: File[]) => void;
  /** Single-file mode: the selected file + its object URL render an inline preview. */
  file?: File | null;
  previewUrl?: string;
  /** Single-file mode: clear the current selection. */
  onClear?: () => void;
};

const kb = (bytes: number) =>
  bytes < 1024 * 1024
    ? `${Math.max(1, Math.round(bytes / 1024))} KB`
    : `${(bytes / (1024 * 1024)).toFixed(1)} MB`;

const matchesAccept = (file: File, accept?: string) => {
  if (!accept) return true;
  // Only image/* is used in this app; keep the check simple and forgiving.
  return accept.includes("image/") ? file.type.startsWith("image/") : true;
};

/**
 * Styled file picker with click + drag-and-drop. In single-file mode (file +
 * previewUrl) it shows an inline thumbnail card with Replace/Remove; otherwise
 * it renders a drop target that appends to the caller's list.
 */
export default function DropZone({
  ariaLabel,
  hint,
  accept = "image/*",
  multiple = false,
  disabled = false,
  onFiles,
  file = null,
  previewUrl,
  onClear,
}: DropZoneProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragOver, setDragOver] = useState(false);
  const descId = useId();

  const open = () => inputRef.current?.click();

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setDragOver(false);
    if (disabled) return;
    const picked = Array.from(e.dataTransfer.files).filter((f) => matchesAccept(f, accept));
    if (picked.length) onFiles(multiple ? picked : picked.slice(0, 1));
  };

  const hidden = (
    <input
      ref={inputRef}
      type="file"
      accept={accept}
      multiple={multiple}
      aria-label={ariaLabel}
      className="sr-only"
      disabled={disabled}
      onChange={(e) => {
        const picked = Array.from(e.target.files ?? []);
        if (picked.length) onFiles(picked);
        e.target.value = "";
      }}
    />
  );

  // Single-file mode with a selection → inline preview card.
  if (file && previewUrl) {
    return (
      <div className="flex items-center gap-3 rounded-xl border border-line bg-surface-2 p-2.5">
        <img
          src={previewUrl}
          alt={`${ariaLabel} preview`}
          className="h-14 w-14 shrink-0 rounded-lg border border-line object-cover"
        />
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-medium text-ink">{file.name}</p>
          <p className="text-xs text-subtle">{kb(file.size)}</p>
        </div>
        <button
          type="button"
          onClick={open}
          disabled={disabled}
          className="rounded-lg border border-line px-3 py-2 text-xs font-medium text-muted transition-colors hover:border-line-strong hover:bg-surface disabled:opacity-50"
        >
          Replace
        </button>
        {onClear && (
          <button
            type="button"
            onClick={onClear}
            disabled={disabled}
            aria-label={`Remove ${file.name}`}
            className="grid h-9 w-9 shrink-0 place-items-center rounded-lg text-subtle transition-colors hover:bg-danger-soft hover:text-danger disabled:opacity-50"
          >
            <TrashIcon size={16} />
          </button>
        )}
        {hidden}
      </div>
    );
  }

  // Empty / multi-file add state → drop target.
  return (
    <>
      <button
        type="button"
        onClick={open}
        disabled={disabled}
        aria-describedby={hint ? descId : undefined}
        onDragOver={(e) => {
          e.preventDefault();
          if (!disabled) setDragOver(true);
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={handleDrop}
        className={`flex w-full flex-col items-center justify-center gap-2 rounded-xl border border-dashed px-4 py-6 text-center transition-colors ${
          dragOver
            ? "border-accent bg-accent-soft"
            : "border-line-strong bg-surface-2 hover:border-accent hover:bg-accent-soft/40"
        } disabled:cursor-not-allowed disabled:opacity-50`}
      >
        <span
          className={`grid h-10 w-10 place-items-center rounded-full transition-colors ${
            dragOver ? "bg-accent text-accent-on" : "bg-surface text-muted"
          }`}
        >
          {multiple ? <ImageIcon size={20} /> : <UploadIcon size={20} />}
        </span>
        <span className="text-sm font-medium text-ink">
          {dragOver ? "Drop to upload" : (
            <>
              <span className="text-accent">Click to browse</span> or drag &amp; drop
            </>
          )}
        </span>
        {hint && (
          <span id={descId} className="text-xs text-subtle">
            {hint}
          </span>
        )}
      </button>
      {hidden}
    </>
  );
}
