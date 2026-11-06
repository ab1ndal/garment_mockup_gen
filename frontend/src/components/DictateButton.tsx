import { useEffect, useRef, useState } from "react";
import { MicIcon } from "./icons";

// Web Speech API: Chrome/Edge/Safari ship it (Chrome prefixed); Firefox does not.
// TypeScript's DOM lib has no SpeechRecognition constructor type, so declare the
// small surface used here.
type RecognitionResultEvent = {
  resultIndex: number;
  results: ArrayLike<{ isFinal: boolean; 0: { transcript: string } }>;
};
type Recognition = {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  onresult: ((e: RecognitionResultEvent) => void) | null;
  onerror: ((e: { error: string }) => void) | null;
  onend: (() => void) | null;
  start: () => void;
  stop: () => void;
};
type RecognitionCtor = new () => Recognition;

function recognitionCtor(): RecognitionCtor | null {
  const w = window as unknown as {
    SpeechRecognition?: RecognitionCtor;
    webkitSpeechRecognition?: RecognitionCtor;
  };
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null;
}

// Indian English recognises mixed Hindi garment terms (dupatta, zari) better than en-US.
const LANG = "en-IN";

const ERROR_TEXT: Record<string, string> = {
  "not-allowed": "Microphone access is blocked. Allow it in the browser's site settings.",
  "service-not-allowed": "Microphone access is blocked. Allow it in the browser's site settings.",
  "no-speech": "No speech heard. Tap the mic and try again.",
  "audio-capture": "No microphone found.",
  network: "Dictation needs an internet connection.",
};

/** Mic toggle that appends spoken text to a field. Renders nothing where the
 *  browser has no speech recognition (e.g. Firefox). */
export default function DictateButton({
  onText, onError,
}: {
  onText: (text: string) => void;
  onError: (msg: string) => void;
}) {
  const [listening, setListening] = useState(false);
  const recRef = useRef<Recognition | null>(null);
  const Ctor = recognitionCtor();

  useEffect(() => () => recRef.current?.stop(), []);

  if (!Ctor) return null;

  const toggle = () => {
    if (listening) {
      recRef.current?.stop();
      return;
    }
    const rec = new Ctor();
    rec.lang = LANG;
    rec.continuous = true;
    rec.interimResults = false;
    rec.onresult = (e) => {
      let text = "";
      for (let i = e.resultIndex; i < e.results.length; i++) {
        if (e.results[i].isFinal) text += e.results[i][0].transcript;
      }
      if (text.trim()) onText(text.trim());
    };
    rec.onerror = (e) => {
      if (e.error !== "aborted") onError(ERROR_TEXT[e.error] ?? `Dictation failed: ${e.error}`);
    };
    rec.onend = () => setListening(false);
    recRef.current = rec;
    rec.start();
    setListening(true);
  };

  return (
    <button
      type="button"
      className={listening ? "btn-refine dictate-live" : "btn-refine"}
      onClick={toggle}
      aria-pressed={listening}
      aria-label={listening ? "Stop dictation" : "Dictate"}
    >
      <MicIcon />
      {listening ? "Stop" : "Dictate"}
    </button>
  );
}
