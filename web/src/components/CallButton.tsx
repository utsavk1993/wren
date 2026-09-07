import { useEffect, useRef } from "react";

import type { CallState } from "../types";

/**
 * The call, as one control.
 *
 * The rings move with whoever is talking. On a call there is nothing else to
 * look at, and not knowing whether the microphone is picking anything up is the
 * most disorienting thing about using one of these. A ring that answers your
 * voice settles that before anyone has to ask.
 */

const RINGS = 3;

/** The rings are driven from JavaScript, so the stylesheet cannot switch them
 *  off. Anyone who has asked the system for less motion has to be asked here. */
function prefersReducedMotion(): boolean {
  return window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;
}

export function CallButton({
  state,
  level,
  onStart,
  onEnd,
}: {
  state: CallState;
  /** How loud, right now, from nothing to one. */
  level: () => number;
  onStart: () => void;
  onEnd: () => void;
}) {
  const rings = useRef<(HTMLSpanElement | null)[]>([]);
  const onCall = state !== "idle" && state !== "ended";

  useEffect(() => {
    // Hand the rings back to the stylesheet, so they do not stay frozen at
    // whatever the last frame left behind once the call is over.
    const clear = () => {
      for (const ring of rings.current) {
        if (!ring) continue;
        ring.style.transform = "";
        ring.style.opacity = "";
      }
    };

    if (!onCall) {
      clear();
      return;
    }

    if (prefersReducedMotion()) {
      // Still rings, held steady. They say the call is up without moving.
      rings.current.forEach((ring, i) => {
        if (ring) ring.style.opacity = `${Math.max(0, 0.3 - i * 0.1)}`;
      });
      return clear;
    }

    let frame = 0;
    // Driven from the audio itself rather than a fixed animation, so the
    // movement means something: it is the sound, not decoration.
    const tick = () => {
      const loudness = level();
      rings.current.forEach((ring, i) => {
        if (!ring) return;
        const spread = 1 + loudness * (0.35 + i * 0.28);
        ring.style.transform = `scale(${spread})`;
        ring.style.opacity = `${Math.max(0, (0.5 - i * 0.14) * (0.25 + loudness))}`;
      });
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => {
      cancelAnimationFrame(frame);
      clear();
    };
  }, [onCall, level]);

  const label =
    state === "idle" ? "Call" : state === "ended" ? "Call again" : "End";

  return (
    <div className={`callbutton ${state}`}>
      <div className="rings">
        {Array.from({ length: RINGS }, (_, i) => (
          <span
            key={i}
            className="ring"
            ref={(node) => {
              rings.current[i] = node;
            }}
          />
        ))}
        <button
          type="button"
          className="dial"
          onClick={onCall ? onEnd : onStart}
          aria-label={onCall ? "End the call" : "Start a call"}
        >
          {onCall ? <EndIcon /> : <PhoneIcon />}
        </button>
      </div>
      <span className="dial-label">{label}</span>
    </div>
  );
}

function PhoneIcon() {
  return (
    <svg viewBox="0 0 24 24" width="26" height="26" fill="currentColor" aria-hidden>
      <path d="M6.6 10.8a15.1 15.1 0 0 0 6.6 6.6l2.2-2.2a1 1 0 0 1 1-.24 11.4 11.4 0 0 0 3.6.58 1 1 0 0 1 1 1V20a1 1 0 0 1-1 1A17 17 0 0 1 3 4a1 1 0 0 1 1-1h3.5a1 1 0 0 1 1 1c0 1.25.2 2.46.57 3.6a1 1 0 0 1-.25 1z" />
    </svg>
  );
}

function EndIcon() {
  return (
    <svg viewBox="0 0 24 24" width="26" height="26" fill="currentColor" aria-hidden>
      <path d="M12 9c-1.8 0-3.5.3-5.1.8v3.4c0 .4-.2.8-.6 1-.9.5-1.8 1.1-2.6 1.8-.2.2-.4.3-.7.3s-.5-.1-.7-.3l-2-2a.9.9 0 0 1-.3-.7c0-.3.1-.5.3-.7C3.5 9.6 7.5 8 12 8s8.5 1.6 11.7 4.6c.2.2.3.4.3.7s-.1.5-.3.7l-2 2c-.2.2-.4.3-.7.3s-.5-.1-.7-.3c-.8-.7-1.7-1.3-2.6-1.8a1.1 1.1 0 0 1-.6-1V9.8C15.5 9.3 13.8 9 12 9z" />
    </svg>
  );
}
