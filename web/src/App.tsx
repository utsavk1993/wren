import { useEffect, useRef, useState } from "react";

import { useCall } from "./hooks/useCall";
import type { CallState } from "./types";

const STATE_LABEL: Record<CallState, string> = {
  idle: "Not connected",
  connecting: "Connecting",
  listening: "Listening",
  thinking: "Working on it",
  speaking: "Speaking",
  ended: "Call ended",
};

export function App() {
  const { state, lines, timing, capabilities, error, start, hangUp, say } = useCall();
  const [draft, setDraft] = useState("");
  const transcriptEnd = useRef<HTMLDivElement>(null);
  const onCall = state !== "idle" && state !== "ended";

  useEffect(() => {
    transcriptEnd.current?.scrollIntoView({ behavior: "smooth" });
  }, [lines]);

  const send = (event: React.FormEvent) => {
    event.preventDefault();
    const text = draft.trim();
    if (!text || !onCall) return;
    say(text);
    setDraft("");
  };

  // Speech is optional. Without it the call still works by typing, which is
  // said plainly rather than leaving a dead microphone button on screen.
  const canSpeak = capabilities?.speech_in && capabilities?.speech_out;

  return (
    <div className="app">
      <header>
        <div>
          <h1>Wren</h1>
          <p className="sub">Home security support</p>
        </div>
        <div className="status">
          <span className={`dot ${state}`} aria-hidden />
          <span>{STATE_LABEL[state]}</span>
        </div>
      </header>

      {error && <p className="error">{error}</p>}

      <main className="transcript" aria-live="polite">
        {lines.length === 0 && !onCall && (
          <p className="empty">
            Start a call to talk to the agent.
            {capabilities && !canSpeak && (
              <> Speech isn't configured on this deployment, so the call is typed.</>
            )}
          </p>
        )}
        {lines.map((line) => (
          <div
            key={line.id}
            className={`line ${line.speaker}${line.acknowledgement ? " filler" : ""}`}
          >
            <span className="who">{line.speaker === "agent" ? "Wren" : "You"}</span>
            <p>{line.text}</p>
          </div>
        ))}
        <div ref={transcriptEnd} />
      </main>

      {timing && (
        <p className="timing">
          {timing.ms_total} ms
          {timing.llm_rounds ? ` · ${timing.llm_rounds} model rounds` : ""}
          {timing.over_budget ? ` · over budget: ${timing.over_budget}` : ""}
        </p>
      )}

      <form className="composer" onSubmit={send}>
        {!onCall ? (
          <button type="button" className="primary" onClick={start}>
            {state === "ended" ? "Call again" : "Call"}
          </button>
        ) : (
          <>
            <input
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              placeholder="Say something"
              autoFocus
            />
            <button type="submit" disabled={!draft.trim()}>
              Send
            </button>
            <button type="button" className="hangup" onClick={hangUp}>
              Hang up
            </button>
          </>
        )}
      </form>
    </div>
  );
}
