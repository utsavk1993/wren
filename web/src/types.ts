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
  ms_first_token_per_round?: number;
  ms_systems?: number;
  ms_retrieval?: number;
  llm_rounds?: number;
  over_budget?: string;
};

export type CallState = "idle" | "connecting" | "listening" | "thinking" | "speaking" | "ended";

/** A past call, as it appears in the list. */
export type CallSummary = {
  id: string;
  customer_external_id: string | null;
  verified: boolean;
  escalated: boolean;
  started_at: string;
  ended_at: string | null;
  turns: number;
  tool_calls: number;
  refusals: number;
  total_ms: string;
};

export type RecordedTurn = { speaker: "caller" | "agent"; text: string; at: string };

export type RecordedToolCall = {
  name: string;
  /** What the model asked for. A lookup that found nothing reads very
   *  differently once you can see what it was asked about. */
  arguments: Record<string, unknown>;
  result: Record<string, unknown>;
  ms: number;
  /** Which caller turn caused it, so it can be shown in the right place. */
  turn: number;
};

/** One call in full. */
export type CallDetail = CallSummary & {
  transcript: RecordedTurn[];
  tool_calls_detail: RecordedToolCall[];
  refusals_detail: string[];
  timings: Timing[];
};

/** What each stage of a turn is allowed before the call stops feeling live. */
export const STAGE_BUDGETS_MS: Record<string, number> = {
  ms_endpointing: 400,
  ms_transcription: 300,
  ms_retrieval: 150,
  ms_systems: 250,
  ms_first_token: 500,
  ms_first_audio: 150,
};

export const STAGE_LABELS: Record<string, string> = {
  ms_endpointing: "Deciding they'd finished",
  ms_transcription: "Transcribing",
  ms_retrieval: "Searching the knowledge base",
  ms_systems: "Customer and equipment lookups",
  ms_first_token: "Model, to its first word",
  ms_first_audio: "Speech synthesis",
};
