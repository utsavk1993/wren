import { useCallback, useEffect, useRef, useState } from "react";

import type { CallState, Capabilities, Line, Timing } from "../types";

const AGENT_URL = import.meta.env.VITE_AGENT_BASE_URL ?? "http://localhost:7860";

function socketUrl(): string {
  const base = AGENT_URL.replace(/^http/, "ws");
  return `${base}/call`;
}

/**
 * Holds one call.
 *
 * The transcript is kept here rather than in the page so that a re-render never
 * loses part of a conversation, and every message from the agent is applied in
 * the order it arrived.
 */
export function useCall() {
  const socket = useRef<WebSocket | null>(null);
  const nextId = useRef(0);

  const [state, setState] = useState<CallState>("idle");
  const [lines, setLines] = useState<Line[]>([]);
  const [timing, setTiming] = useState<Timing | null>(null);
  const [capabilities, setCapabilities] = useState<Capabilities | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetch(`${AGENT_URL}/capabilities`)
      .then((r) => r.json())
      .then(setCapabilities)
      .catch(() => setError("Can't reach the agent."));
  }, []);

  const addLine = useCallback((line: Omit<Line, "id">) => {
    setLines((current) => [...current, { ...line, id: nextId.current++ }]);
  }, []);

  const hangUp = useCallback(() => {
    socket.current?.send(JSON.stringify({ type: "hangup" }));
    socket.current?.close();
    socket.current = null;
    setState("ended");
  }, []);

  const start = useCallback(() => {
    setLines([]);
    setTiming(null);
    setError(null);
    setState("connecting");

    const ws = new WebSocket(socketUrl());
    socket.current = ws;

    ws.onopen = () => setState("listening");
    ws.onerror = () => {
      setError("The call dropped.");
      setState("ended");
    };
    ws.onclose = () => setState((s) => (s === "ended" ? s : "ended"));

    ws.onmessage = (event) => {
      const message = JSON.parse(event.data);
      switch (message.type) {
        case "ready":
          setCapabilities(message);
          break;
        case "caller_said":
          addLine({ speaker: "caller", text: message.text });
          setState("thinking");
          break;
        case "agent_said":
          addLine({
            speaker: "agent",
            text: message.text,
            acknowledgement: message.acknowledgement,
          });
          // A filler means work is still going on, so the state stays as it is.
          setState(message.acknowledgement ? "thinking" : "speaking");
          break;
        case "timing":
          setTiming(message);
          setState("listening");
          break;
        case "interrupted":
          setState("listening");
          break;
      }
    };
  }, [addLine]);

  const say = useCallback((text: string) => {
    if (!socket.current || socket.current.readyState !== WebSocket.OPEN) return;
    socket.current.send(JSON.stringify({ type: "text", text }));
  }, []);

  const interrupt = useCallback(() => {
    socket.current?.send(JSON.stringify({ type: "interrupt" }));
  }, []);

  useEffect(() => {
    // Closing the socket if the component goes away, so a call does not stay
    // open behind a page nobody is looking at.
    return () => socket.current?.close();
  }, []);

  return { state, lines, timing, capabilities, error, start, hangUp, say, interrupt };
}
