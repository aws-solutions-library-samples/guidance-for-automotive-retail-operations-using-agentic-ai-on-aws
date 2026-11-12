"use client";

// useVoiceAssistant (spec §9A AR-7): one hook for A3 live-transcript objection
// detection, A6/A7 dictation, and chat voice input.
//
// Engines, in order of preference:
//   transcribe — Amazon Transcribe Streaming (when an identity pool is configured)
//   browser    — Web Speech API (dev / fallback)
//   replay     — transcript-replay demo mode (synthetic-data plan D1): plays a
//                scripted transcript line by line, no microphone needed
// Speech output uses speechSynthesis (Polly voices are served by the backend
// when configured).

import { useCallback, useEffect, useRef, useState } from "react";
import { startTranscribe, transcribeConfig } from "./transcribe";

export type VoiceEngine = "transcribe" | "browser" | "replay" | "none";

export interface VoiceOptions {
  language?: string;            // BCP-47, from MarketConfig.locale
  continuous?: boolean;
  onFinal?: (text: string) => void;   // each final utterance (e.g. → /utterance)
  replayScript?: string[];      // enables replay mode
  replayIntervalMs?: number;
}

export function useVoiceAssistant(opts: VoiceOptions = {}) {
  const { language = "en-US", continuous = true, onFinal, replayScript, replayIntervalMs = 3000 } = opts;
  const [listening, setListening] = useState(false);
  const [interim, setInterim] = useState("");
  const [transcript, setTranscript] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [engine, setEngine] = useState<VoiceEngine>("none");
  const stopRef = useRef<null | (() => void)>(null);
  const finalRef = useRef(onFinal);
  finalRef.current = onFinal;

  useEffect(() => {
    if (replayScript?.length) setEngine("replay");
    else if (transcribeConfig(language)) setEngine("transcribe");
    else if (typeof window !== "undefined" && ((window as any).SpeechRecognition || (window as any).webkitSpeechRecognition)) setEngine("browser");
    else setEngine("none");
  }, [language, replayScript]);

  const emitFinal = useCallback((text: string) => {
    const t = text.trim();
    if (!t) return;
    setTranscript((xs) => [...xs, t]);
    setInterim("");
    finalRef.current?.(t);
  }, []);

  const stop = useCallback(() => {
    stopRef.current?.();
    stopRef.current = null;
    setListening(false);
  }, []);

  const start = useCallback(async () => {
    setError(null);
    if (stopRef.current) return;
    try {
      if (engine === "replay" && replayScript?.length) {
        let i = 0;
        const t = setInterval(() => {
          if (i >= replayScript.length) { clearInterval(t); stopRef.current = null; setListening(false); return; }
          emitFinal(replayScript[i++]);
        }, replayIntervalMs);
        stopRef.current = () => clearInterval(t);
      } else if (engine === "transcribe") {
        const cfg = transcribeConfig(language)!;
        stopRef.current = await startTranscribe(cfg, (text, isFinal) => (isFinal ? emitFinal(text) : setInterim(text)),
                                                (e) => { setError(e.message); stop(); });
      } else if (engine === "browser") {
        const SR = (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;
        const rec = new SR();
        rec.lang = language;
        rec.continuous = continuous;
        rec.interimResults = true;
        rec.onresult = (ev: any) => {
          for (let i = ev.resultIndex; i < ev.results.length; i++) {
            const r = ev.results[i];
            if (r.isFinal) emitFinal(r[0].transcript); else setInterim(r[0].transcript);
          }
        };
        rec.onerror = (ev: any) => { setError(ev.error || "speech recognition error"); };
        rec.onend = () => { stopRef.current = null; setListening(false); };
        rec.start();
        stopRef.current = () => rec.stop();
      } else {
        setError("Voice input is not available in this browser.");
        return;
      }
      setListening(true);
    } catch (e: any) {
      setError(e?.message || String(e));
      stop();
    }
  }, [engine, replayScript, replayIntervalMs, language, continuous, emitFinal, stop]);

  useEffect(() => stop, [stop]);

  const speak = useCallback((text: string) => {
    if (typeof window === "undefined" || !window.speechSynthesis) return;
    const u = new SpeechSynthesisUtterance(text);
    u.lang = language;
    window.speechSynthesis.cancel();
    window.speechSynthesis.speak(u);
  }, [language]);

  return { engine, listening, interim, transcript, error, start, stop, toggle: listening ? stop : start, speak,
           reset: () => setTranscript([]) };
}
