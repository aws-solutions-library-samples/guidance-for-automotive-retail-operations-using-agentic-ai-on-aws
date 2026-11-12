"use client";

import { createContext, ReactNode, useCallback, useContext, useState } from "react";

type Tone = "info" | "success" | "error";
interface Toast { id: number; text: ReactNode; tone: Tone }
const Ctx = createContext<(text: ReactNode, tone?: Tone) => void>(() => {});

/** App-wide toast stack (aria-live) — every write action reports its outcome. */
export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<Toast[]>([]);
  const push = useCallback((text: ReactNode, tone: Tone = "info") => {
    const id = Date.now() + Math.random();
    setItems((xs) => [...xs, { id, text, tone }]);
    setTimeout(() => setItems((xs) => xs.filter((x) => x.id !== id)), tone === "error" ? 8000 : 4000);
  }, []);
  return (
    <Ctx.Provider value={push}>
      {children}
      <div className="toast-stack" aria-live="polite" role="status">
        {items.map((t) => <div key={t.id} className={`toast ${t.tone}`}>{t.text}</div>)}
      </div>
    </Ctx.Provider>
  );
}

export const useToast = () => useContext(Ctx);
