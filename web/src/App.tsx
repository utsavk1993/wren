import { useEffect, useRef, useState } from "react";

import { CallButton } from "./components/CallButton";
import { CallDetail } from "./components/CallDetail";
import { CallList } from "./components/CallList";
import { ScriptPanel } from "./components/ScriptPanel";
import { useCall } from "./hooks/useCall";
import { useCallDetail, useCallList } from "./hooks/useCalls";
import type { CallState } from "./types";

const STATE_LABEL: Record<CallState, string> = {
  idle: "Ready when you are",
  connecting: "Connecting",
  listening: "Listening",
  thinking: "One moment",
  speaking: "Wren is speaking",
  ended: "Call ended",
};

type Tab = "call" | "history";

function LiveCall() {
  const {
    state, lines, hearing, timing, capabilities, error, refused, access,
    start, hangUp, say, level,
  } = useCall();
  const [draft, setDraft] = useState("");
  const [passphrase, setPassphrase] = useState("");
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
      <div className="stage">
        <CallButton
          state={state}
          level={level}
          onStart={() => start(access?.guarded ? passphrase : undefined)}
          onEnd={hangUp}
        />
        <p className={`stage-state ${state}`}>{STATE_LABEL[state]}</p>
        {!onCall && access?.guarded && (
          <input
            className="passphrase"
            value={passphrase}
            onChange={(e) => setPassphrase(e.target.value)}
            placeholder="Passphrase"
            aria-label="Passphrase"
          />
        )}
        {refused && <p className="error">{refused}</p>}
        {state === "connecting" && (
          // A host that sleeps takes about a minute to come back, and a button
          // that does nothing for that long reads as broken.
          <p className="stage-hint">
            Connecting. If this has been quiet for a while it may take up to a
            minute to wake up.
          </p>
        )}
        {!onCall && (
          <p className="stage-hint">
            {canSpeak
              ? "Allow the microphone and just talk."
              : "Speech isn't configured here, so the call is typed."}
          </p>
        )}
        {error && <p className="error">{error}</p>}
      </div>

      <main className="transcript" aria-live="polite">
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

      {onCall && (typing || !canSpeak) && (
        <form className="composer" onSubmit={send}>
          <input
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder="Type instead"
            autoFocus
          />
          <button type="submit" disabled={!draft.trim()}>
            Send
          </button>
          {canSpeak && (
            <button type="button" onClick={() => setTyping(false)}>
              Speak
            </button>
          )}
        </form>
      )}
      {onCall && canSpeak && !typing && (
        <button type="button" className="switch" onClick={() => setTyping(true)}>
          Type instead
        </button>
      )}
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

      {tab === "call" ? (
        <div className="with-scripts">
          <div className="call-column">
            <LiveCall />
          </div>
          <ScriptPanel />
        </div>
      ) : (
        <History />
      )}
    </div>
  );
}
