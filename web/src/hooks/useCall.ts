import { useCallback, useEffect, useRef, useState } from "react";

import { Playback } from "../audio";
import { Microphone } from "../microphone";
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
  const playback = useRef(new Playback());
  const microphone = useRef(new Microphone());
  const nextId = useRef(0);

  const [state, setState] = useState<CallState>("idle");
  const [lines, setLines] = useState<Line[]>([]);
  const [timing, setTiming] = useState<Timing | null>(null);
  const [capabilities, setCapabilities] = useState<Capabilities | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [hearing, setHearing] = useState("");
  const [refused, setRefused] = useState<string | null>(null);
  const [access, setAccess] = useState<{ guarded: boolean } | null>(null);

  useEffect(() => {
    fetch(`${AGENT_URL}/capabilities`)
      .then((r) => r.json())
      .then(setCapabilities)
      .catch(() => setError("Can't reach the agent."));
    fetch(`${AGENT_URL}/access`)
      .then((r) => r.json())
      .then(setAccess)
      .catch(() => undefined);
  }, []);

  const addLine = useCallback((line: Omit<Line, "id">) => {
    setLines((current) => [...current, { ...line, id: nextId.current++ }]);
  }, []);

  const hangUp = useCallback(() => {
    playback.current.stop();
    void microphone.current.stop();
    socket.current?.send(JSON.stringify({ type: "hangup" }));
    socket.current?.close();
    socket.current = null;
    setState("ended");
  }, []);

  const start = useCallback((passphrase?: string) => {
    setLines([]);
    setTiming(null);
    setError(null);
    setRefused(null);
    setState("connecting");
    // Started from inside the click that begins the call. A browser will not
    // produce sound until the person has interacted with the page, so doing
    // this any later means the first reply is silent.
    playback.current.start();

    const ws = new WebSocket(socketUrl());
    socket.current = ws;

    ws.onopen = () => {
      // Sent before anything else, because the far end will not talk without it.
      if (passphrase !== undefined) {
        ws.send(JSON.stringify({ passphrase }));
      }
      setState("listening");
      // Only once the socket is open, or the first words are captured and
      // dropped before there is anywhere to send them.
      void microphone.current
        .start((pcm16) => {
          if (ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ type: "audio", pcm16 }));
          }
        })
        .catch((cause) =>
          setError(
            cause instanceof DOMException && cause.name === "NotAllowedError"
              ? "The microphone was blocked. Allow it and call again."
              : `Couldn't open the microphone: ${cause}`,
          ),
        );
    };
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
        case "refused":
          // Turned away rather than broken, so it is said plainly instead of
          // looking like a failure.
          setRefused(message.reason);
          setState("ended");
          void microphone.current.stop();
          break;
        case "hearing":
          // What is being picked up, before the turn is judged finished.
          setHearing(message.text);
          break;
        case "caller_said":
          setHearing("");
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
        case "audio":
          playback.current.play(message.pcm16);
          break;
        case "timing":
          setTiming(message);
          setState("listening");
          break;
        case "interrupted":
          playback.current.stop();
          setState("listening");
          break;
        case "hangup":
          // The agent ended the call, because the caller asked it to. The
          // playback is deliberately left alone: the closing line has already
          // been queued ahead of real time, and stopping it here would cut the
          // goodbye off mid-word. The microphone goes now, since nothing more
          // is going to be listened to.
          void microphone.current.stop();
          socket.current?.close();
          socket.current = null;
          setState("ended");
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

  /** Whoever is talking, as a number between nothing and one.
   *
   * Read on demand rather than kept in state. It changes every frame, and
   * putting it in state would re-render the page sixty times a second to move
   * a ring.
   */
  const level = useCallback(
    () => Math.max(playback.current.level, microphone.current.level),
    [],
  );

  useEffect(() => {
    // Closing the socket and stopping any sound if the component goes away, so
    // a call does not carry on behind a page nobody is looking at.
    const audio = playback.current;
    const mic = microphone.current;
    return () => {
      socket.current?.close();
      void audio.close();
      void mic.stop();
    };
  }, []);

  return {
    state,
    lines,
    hearing,
    timing,
    capabilities,
    error,
    refused,
    access,
    start,
    hangUp,
    say,
    interrupt,
    level,
  };
}
