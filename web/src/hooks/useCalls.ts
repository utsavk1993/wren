import { useCallback, useEffect, useState } from "react";

import type { CallDetail, CallSummary } from "../types";

const AGENT_URL = import.meta.env.VITE_AGENT_BASE_URL ?? "http://localhost:7860";

// Often enough that a call in progress visibly grows, rarely enough that
// nobody notices the requests.
const FOLLOW_INTERVAL_MS = 2000;

export function useCallList({ follow = true }: { follow?: boolean } = {}) {
  const [calls, setCalls] = useState<CallSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const response = await fetch(`${AGENT_URL}/calls`);
      if (!response.ok) throw new Error(`the agent answered ${response.status}`);
      setCalls(await response.json());
      setError(null);
    } catch (cause) {
      setError(
        cause instanceof Error ? cause.message : "Can't reach the agent.",
      );
      // An empty list and a failed request look identical otherwise, and the
      // difference is the whole diagnosis.
      setCalls([]);
    }
  }, []);

  useEffect(() => {
    void refresh();
    if (!follow) return;
    const timer = setInterval(() => void refresh(), FOLLOW_INTERVAL_MS);
    return () => clearInterval(timer);
  }, [refresh, follow]);

  return { calls, error, refresh };
}

export function useCallDetail(callId: string | null, { follow = true } = {}) {
  const [call, setCall] = useState<CallDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!callId) {
      setCall(null);
      return;
    }
    let cancelled = false;

    const load = async () => {
      try {
        const response = await fetch(`${AGENT_URL}/calls/${callId}`);
        if (!response.ok) throw new Error(`the agent answered ${response.status}`);
        const raw = await response.json();
        if (cancelled) return;
        setCall({
          ...raw,
          // The list and the detail use the same column names for different
          // things: counts in one, contents in the other.
          tool_calls_detail: raw.tool_calls ?? [],
          refusals_detail: raw.refusals ?? [],
        });
        setError(null);
      } catch (cause) {
        if (!cancelled) {
          setError(cause instanceof Error ? cause.message : "Couldn't load that call.");
        }
      }
    };

    void load();
    // A call still running grows while it is being watched.
    const timer = follow ? setInterval(() => void load(), FOLLOW_INTERVAL_MS) : null;
    return () => {
      cancelled = true;
      if (timer) clearInterval(timer);
    };
  }, [callId, follow]);

  return { call, error };
}
