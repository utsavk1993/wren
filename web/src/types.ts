export type Capabilities = {
  transport: string;
  sample_rate_hz: number;
  speech_in: boolean;
  speech_out: boolean;
  conversation: boolean;
};

export type Line = {
  id: number;
  speaker: "caller" | "agent";
  text: string;
  /** Filler said while a lookup runs, shown differently from a real answer. */
  acknowledgement?: boolean;
};

export type Timing = {
  ms_total?: number;
  ms_first_token?: number;
  llm_rounds?: number;
  over_budget?: string;
};

/** What the call is doing, so the caller is never left wondering. */
export type CallState = "idle" | "connecting" | "listening" | "thinking" | "speaking" | "ended";
