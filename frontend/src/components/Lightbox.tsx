import { useCallback, useEffect, useRef, useState } from "react";
import { getDriveImage } from "../api";

// Full-screen overlay that shows one image enlarged "to a reasonable size for
// comparison". Closing (Esc / backdrop / button) returns to the previous state.
// Layered above modals (see .lightbox-overlay z-index in index.css).
function Lightbox({
  src, alt, loading, onClose,
}: {
  src: string;
  alt: string;
  loading: boolean;
  onClose: () => void;
}) {
  const closeRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    closeRef.current?.focus();
    // Capture + stop so Esc closes only the lightbox, not an underlying modal.
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopImmediatePropagation();
        onClose();
      }
    };
    document.addEventListener("keydown", onKey, true);
    return () => {
      document.removeEventListener("keydown", onKey, true);
      document.body.style.overflow = prevOverflow;
    };
  }, [onClose]);

  return (
    <div
      className="lightbox-overlay"
      role="dialog"
      aria-modal="true"
      aria-label={alt || "Enlarged image"}
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <button ref={closeRef} className="lightbox-close" onClick={onClose} aria-label="Close image">
        <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor"
          strokeWidth="2" strokeLinecap="round" aria-hidden="true">
          <path d="M18 6 6 18M6 6l12 12" />
        </svg>
      </button>
      <figure className="lightbox-figure">
        <img className="lightbox-img" src={src} alt={alt} />
        {loading && (
          <span className="lightbox-loading" role="status">
            <span className="spinner" aria-hidden /> Loading full image…
          </span>
        )}
      </figure>
    </div>
  );
}

type View = { src: string; alt: string; loading: boolean } | null;

// Shared lightbox controller. `show` opens an already-full-res image (e.g. a
// generated mockup); `showDrive` opens a Drive file by id, displaying its small
// thumbnail immediately and swapping in the lazily-fetched full image.
export function useImageLightbox() {
  const [view, setView] = useState<View>(null);
  const cache = useRef(new Map<string, string>());
  const reqId = useRef(0);

  const close = useCallback(() => {
    reqId.current++;
    setView(null);
  }, []);

  const show = useCallback((src: string, alt: string) => {
    reqId.current++;
    setView({ src, alt, loading: false });
  }, []);

  const showDrive = useCallback((fileId: string, alt: string, thumb: string) => {
    const id = ++reqId.current;
    const cached = cache.current.get(fileId);
    if (cached) {
      setView({ src: cached, alt, loading: false });
      return;
    }
    // Show the thumbnail right away (upscaled) while the full image loads.
    setView({ src: thumb, alt, loading: true });
    getDriveImage(fileId)
      .then(({ image_url }) => {
        cache.current.set(fileId, image_url);
        if (reqId.current === id) setView({ src: image_url, alt, loading: false });
      })
      .catch(() => {
        if (reqId.current === id) setView((v) => (v ? { ...v, loading: false } : v));
      });
  }, []);

  const node = view ? <Lightbox {...view} onClose={close} /> : null;
  return { show, showDrive, close, node };
}
