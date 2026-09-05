import { useEffect, useRef, useState } from "react";

import { CallDetail } from "./components/CallDetail";
import { CallList } from "./components/CallList";
import { useCall } from "./hooks/useCall";
import { useCallDetail, useCallList } from "./hooks/useCalls";
import type { CallState } from "./types";

const STATE_LABEL: Record<CallState, string> = {
  idle: "Not connected",
  connecting: "Connecting",
  listening: "Listening",
  thinking: "Working on it",
  speaking: "Speaking",
  ended: "Call ended",
};

type Tab = "call" | "history";

function LiveCall() {
  const { state, lines, timing, capabilities, error, start, hangUp, say } = useCall();
  const [draft, setDraft] = useState("");
  const end = useRef<HTMLDivElement>(null);
  const onCall = state !== "idle" && state !== "ended";

  useEffect(() => end.current?.scrollIntoView({ behavior: "smooth" }), [lines]);

  const send = (event: React.FormEvent) => {
    event.preventDefault();
    const text = draft.trim();
    if (!text || !onCall) return;
    say(text);
    setDraft("");
  };

  const canSpeak = capabilities?.speech_in && capabilities?.speech_out;

  return (
    <>
      <div className="status">
        <span className={`dot ${state}`} aria-hidden />
        <span>{STATE_LABEL[state]}</span>
      </div>

      {error && <p className="error">{error}</p>}

      <main className="transcript" aria-live="polite">
        {lines.length === 0 && !onCall && (
          <p className="empty">
            Start a call to talk to the agent.
            {capabilities && !canSpeak && (
              <> Speech isn't configured here, so the call is typed.</>
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
        <div ref={end} />
      </main>

      {timing && (
        <p className="timing">
          {timing.ms_total} ms
          {timing.llm_rounds ? ` · ${timing.llm_rounds} model round trips` : ""}
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
    </>
  );
}

function History() {
  const [openId, setOpenId] = useState<string | null>(null);
  const { calls, error, refresh } = useCallList();
  const { call } = useCallDetail(openId);

  if (error) return <p className="error">{error}</p>;
  if (openId && call) return <CallDetail call={call} onBack={() => setOpenId(null)} />;
  if (openId) return <p className="empty">Loading call…</p>;
  return <CallList calls={calls} onOpen={setOpenId} onRefresh={refresh} />;
}

export function App() {
  const [tab, setTab] = useState<Tab>("call");

  return (
    <div className="app">
      <header>
        <div>
          <h1>Wren</h1>
          <p className="sub">Home security support</p>
        </div>
        <nav className="tabs">
          <button
            type="button"
            className={tab === "call" ? "on" : ""}
            onClick={() => setTab("call")}
          >
            Call
          </button>
          <button
            type="button"
            className={tab === "history" ? "on" : ""}
            onClick={() => setTab("history")}
          >
            Past calls
          </button>
        </nav>
      </header>

      {tab === "call" ? <LiveCall /> : <History />}
    </div>
  );
}
