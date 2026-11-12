"use client";

// Floating Scenario Replay controller (demo). Mounted by the Shell on every
// authenticated screen; renders only while a replay is active. "Run step"
// executes the step's data-filling act under that step's persona (dev-token
// swap), then hard-navigates to the step's screen so the whole app re-renders
// as that persona. Auto-play keeps going with a short pause per step.

import { useCallback, useEffect, useRef, useState } from "react";
import { hasCognitoSession } from "@/lib/auth";
import { humanizeAgents } from "@/lib/agents";
import { usePersona } from "@/lib/persona";
import {
  ReplayState, StepNote, actAsPersona, clearActAs, loadReplay, makeCaller, saveReplay, scenarioById, stepPersona, stepToken,
} from "@/lib/replay";
import { Btn, cx } from "@/components/app";

const AUTO_PAUSE_MS = 5000;

export function ReplayController() {
  const { persona } = usePersona();
  const [state, setState] = useState<ReplayState | null>(null);
  const [running, setRunning] = useState(false);
  const [lastNotes, setLastNotes] = useState<StepNote[]>([]);
  const [showHistory, setShowHistory] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => { setState(loadReplay()); }, []);

  const stop = useCallback(() => {
    if (timer.current) clearTimeout(timer.current);
    saveReplay(null);
    setState(null);
    clearActAs();
  }, []);

  const runNext = useCallback(async () => {
    const s = loadReplay();
    if (!s || running) return;
    const scenario = scenarioById(s.scenarioId);
    const step = scenario?.steps[s.next];
    if (!scenario || !step) { stop(); return; }
    setRunning(true);
    try {
      // Act as the step's persona BEFORE the API calls — dev-token swap
      // locally, admin View-as (X-Act-As) under a real Cognito session.
      actAsPersona(stepToken(step, s.ctx));
      const notes: StepNote[] = [];
      let ctx = s.ctx;
      if (step.act) {
        const add = await step.act(ctx, makeCaller(notes));
        ctx = { ...ctx, ...(add || {}) };
      }
      const nextState: ReplayState = {
        ...s, ctx, next: s.next + 1,
        notes: { ...s.notes, [step.id]: notes },
        log: [...(s.log || []), {
          stepId: step.id, title: step.title, persona: stepPersona(step, ctx),
          at: new Date().toISOString(), notes,
        }],
      };
      saveReplay(nextState);
      setLastNotes(notes);
      // Hard navigation so the Shell re-reads the persona for the new token.
      window.location.assign(step.screen(ctx));
    } catch {
      setRunning(false);
    }
  }, [running, stop]);

  // Auto-play: after each navigation the controller remounts; if auto is on
  // and there is a step to run, schedule it.
  useEffect(() => {
    if (!state?.auto) return;
    const scenario = scenarioById(state.scenarioId);
    if (!scenario || state.next >= scenario.steps.length) return;
    timer.current = setTimeout(() => { void runNext(); }, AUTO_PAUSE_MS);
    return () => { if (timer.current) clearTimeout(timer.current); };
  }, [state, runNext]);

  if (!state) return null;
  const scenario = scenarioById(state.scenarioId);
  if (!scenario) return null;
  // Under a real Cognito session, persona hops ride the admin View-as header,
  // which the server honors only for admin callers.
  const needsAdmin = hasCognitoSession() && !!persona
    && !persona.impersonating && !["admin_tenant", "admin_dealer", "platform_admin"].includes(persona.role);
  const total = scenario.steps.length;
  const done = state.next >= total;
  const current = done ? null : scenario.steps[state.next];
  const prev = state.next > 0 ? scenario.steps[state.next - 1] : null;
  const prevNotes = prev ? state.notes[prev.id] || lastNotes : [];

  const toggleAuto = () => {
    const s = loadReplay();
    if (!s) return;
    const n = { ...s, auto: !s.auto };
    saveReplay(n);
    setState(n);
  };

  return (
    <div role="region" aria-label="Scenario replay" aria-live="polite"
         style={{ position: "fixed", left: "50%", bottom: "1rem", transform: "translateX(-50%)",
                  zIndex: 80, width: "min(680px, calc(100vw - 2rem))" }}>
      <div className="glass" style={{
        background: "var(--glass-strong)", border: "1px solid var(--glass-border)",
        borderRadius: "var(--radius-lg)", padding: ".7rem .9rem",
        boxShadow: "inset 0 1px 0 var(--glass-highlight), var(--shadow)" }}>
        <div className="flex items-center gap-2 flex-wrap">
          <span className="panel-badge badge-ai">▶ Replay</span>
          <b className="text-[.8rem]">{humanizeAgents(scenario.name)}</b>
          <span className="dim text-[.72rem]">{done ? `done · ${total}/${total}` : `step ${state.next + 1}/${total}`}</span>
          <span className="ml-auto flex gap-1">
            <Btn kind="ghost" size="sm" onClick={() => setShowHistory((h) => !h)} aria-expanded={showHistory}
                 aria-controls="replay-history">
              ☰ History{state.log?.length ? ` (${state.log.length})` : ""}
            </Btn>
            <Btn kind="ghost" size="sm" onClick={toggleAuto} aria-pressed={state.auto}>
              {state.auto ? "⏸ Auto on" : "▶ Auto"}
            </Btn>
            <Btn kind="ghost" size="sm" onClick={stop}>✕ End</Btn>
          </span>
        </div>
        {showHistory && (
          <div id="replay-history" className="mt-2 overflow-y-auto" style={{ maxHeight: "40vh" }}
               role="log" aria-label="Executed steps">
            {(state.log || []).length === 0 && <div className="empty py-3">No steps executed yet.</div>}
            {(state.log || []).map((e, i) => (
              <div key={`${e.stepId}-${i}`} className="activity-item mb-1.5">
                <span className="activity-time mono">{e.at.slice(11, 19)}</span>
                <span className="min-w-0">
                  <b className="text-[.76rem]">{i + 1}. {humanizeAgents(e.title)}</b>
                  <span className="dim text-[.7rem]"> — {e.persona}</span>
                  {e.notes.length > 0 && (
                    <ul className="m-0 mt-0.5 pl-0 text-[.7rem]" style={{ listStyle: "none" }}>
                      {e.notes.map((n, j) => (
                        <li key={j} className={n.ok ? "muted" : ""} style={n.ok ? undefined : { color: "var(--warning)" }}>
                          {n.ok ? "✓" : "⚠"} {humanizeAgents(n.text)}
                        </li>
                      ))}
                    </ul>
                  )}
                </span>
              </div>
            ))}
          </div>
        )}
        {needsAdmin && (
          <div className="mt-1 text-[.72rem]" style={{ color: "var(--warning)" }} role="note">
            ⚠ Persona hops on this sign-in use admin View-as — sign in as a tenant admin, or steps run as you.
          </div>
        )}
        {prev && (
          <div className="mt-1 text-[.72rem] muted">
            ✓ {humanizeAgents(prev.title)}
            {prevNotes.length > 0 && (
              <span className="dim"> — {prevNotes.map((n) => `${n.ok ? "✓" : "⚠"} ${humanizeAgents(n.text)}`).join(" · ")}</span>
            )}
          </div>
        )}
        {current ? (
          <div className="mt-2 flex items-start gap-3">
            <div className="flex-1 min-w-0">
              <div className="text-[.82rem] font-semibold">{humanizeAgents(current.title)}</div>
              <div className="text-[.74rem] muted">{humanizeAgents(current.narrative)}</div>
              <div className="text-[.7rem] dim mt-0.5">acts as <b>{stepPersona(current, state.ctx)}</b>{current.actLabel ? ` · ${humanizeAgents(current.actLabel)}` : ""}</div>
            </div>
            <Btn kind="primary" size="sm" disabled={running} onClick={() => void runNext()}>
              {running ? "Running…" : state.next === 0 ? "Start" : "Run step"}
            </Btn>
          </div>
        ) : (
          <div className="mt-2 flex items-center gap-3">
            <span className="text-[.8rem]">🎉 Scenario complete — the data it created stays in the sandbox for browsing.</span>
            <Btn kind="secondary" size="sm" className="ml-auto" onClick={stop}>Close</Btn>
          </div>
        )}
        <div className={cx("mt-2 progress-bar")} aria-hidden>
          <div className="progress-fill fill-blue" style={{ width: `${(Math.min(state.next, total) / total) * 100}%` }} />
        </div>
      </div>
    </div>
  );
}
