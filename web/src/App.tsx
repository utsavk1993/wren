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
  const { state, lines, hearing, timing, capabilities, error, start, hangUp, say } =
    useCall();
  const [draft, setDraft] = useState("");
  // Speaking is the point. Typing stays for when there is no microphone, or
  // for working on the conversation without talking out loud.
  const [typing, setTyping] = useState(false);
  const end = useRef<HTMLDivElement>(null);
  const onCall = state !== "idle" && state !== "ended";

  useEffect(() => {
    // A block body, not a concise one. Whatever the expression evaluates to is
    // otherwise handed back to React as the cleanup function, and React calls
    // whatever it is given when the component goes away.
    end.current?.scrollIntoView({ behavior: "smooth" });
  }, [lines]);

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
            {canSpeak
              ? "Call, allow the microphone, and just talk."
              : "Speech isn't configured here, so the call is typed."}
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
        {hearing && (
          <div className="line caller hearing">
            <span className="who">You</span>
            <p>{hearing}</p>
          </div>
        )}
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
            {typing || !canSpeak ? (
              <input
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                placeholder="Type instead"
                autoFocus
              />
            ) : (
              <span className="speaking-hint">
                {state === "speaking"
                  ? "Wren is speaking — talk over it to interrupt"
                  : "Listening. Just talk."}
              </span>
            )}
            {(typing || !canSpeak) && (
              <button type="submit" disabled={!draft.trim()}>
                Send
              </button>
            )}
            {canSpeak && (
              <button type="button" onClick={() => setTyping(!typing)}>
                {typing ? "Speak" : "Type"}
              </button>
            )}
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
  // Both keep following, so a call that is happening right now fills in on
  // screen rather than needing to be asked for again.
  const { calls, error, refresh } = useCallList();
  const { call, error: detailError } = useCallDetail(openId);

  if (openId) {
    if (detailError) return <p className="error">{detailError}</p>;
    if (call) return <CallDetail call={call} onBack={() => setOpenId(null)} />;
    return <p className="empty">Loading call…</p>;
  }

  return (
    <>
      {error && <p className="error">Couldn't load calls: {error}</p>}
      <CallList calls={calls} onOpen={setOpenId} onRefresh={refresh} />
    </>
  );
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
