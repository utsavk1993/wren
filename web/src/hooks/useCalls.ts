import { useCallback, useEffect, useState } from "react";

import type { CallDetail, CallSummary } from "../types";

const AGENT_URL = import.meta.env.VITE_AGENT_BASE_URL ?? "http://localhost:7860";

export function useCallList() {
  const [calls, setCalls] = useState<CallSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(() => {
    fetch(`${AGENT_URL}/calls`)
      .then((r) => r.json())
      .then(setCalls)
      .catch(() => setError("Can't reach the agent."));
  }, []);

  useEffect(refresh, [refresh]);
  return { calls, error, refresh };
}

export function useCallDetail(callId: string | null) {
  const [call, setCall] = useState<CallDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!callId) {
      setCall(null);
      return;
    }
    setCall(null);
    fetch(`${AGENT_URL}/calls/${callId}`)
      .then((r) => (r.ok ? r.json() : Promise.reject()))
      .then((raw) =>
        setCall({
          ...raw,
          // The list and the detail use the same column names for different
          // things — counts in one, contents in the other.
          tool_calls_detail: raw.tool_calls ?? [],
          refusals_detail: raw.refusals ?? [],
        }),
      )
      .catch(() => setError("Couldn't load that call."));
  }, [callId]);

  return { call, error };
}
