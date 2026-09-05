import { useState } from "react";

import type { CallDetail as Detail, RecordedToolCall, Timing } from "../types";
import { STAGE_BUDGETS_MS, STAGE_LABELS } from "../types";

/** The stages of one turn, each against what it was allowed.
 *
 * Bare numbers say little. Next to their budget they say where the time went,
 * which is the only question worth asking of a slow turn.
 */
function TurnTiming({ timing }: { timing: Timing }) {
  const stages = Object.keys(STAGE_BUDGETS_MS).filter(
    (key) => (timing as Record<string, number | undefined>)[key],
  );
  return (
    <div className="timing-block">
      <div className="timing-head">
        <strong>{Math.round(timing.ms_total ?? 0)} ms</strong>
        {timing.llm_rounds ? (
          <span className="muted">
            {timing.llm_rounds} model round trip{timing.llm_rounds > 1 ? "s" : ""}
          </span>
        ) : null}
      </div>
      {stages.map((key) => {
        const spent = (timing as Record<string, number>)[key];
        const budget = STAGE_BUDGETS_MS[key];
        const over = spent > budget;
        return (
          <div key={key} className="stage">
            <span className="stage-name">{STAGE_LABELS[key] ?? key}</span>
            <span className="bar">
              <span
                className={`fill${over ? " over" : ""}`}
                style={{ width: `${Math.min(100, (spent / (budget * 3)) * 100)}%` }}
              />
              <span className="budget-mark" style={{ left: `${100 / 3}%` }} />
            </span>
            <span className={`stage-ms${over ? " over" : ""}`}>
              {Math.round(spent)} / {budget} ms
            </span>
          </div>
        );
      })}
    </div>
  );
}

function ToolCall({ call }: { call: RecordedToolCall }) {
  const [open, setOpen] = useState(false);
  const refused = "refused" in call.result;
  const failed = "error" in call.result;
  const guidance = call.result.guidance as string | undefined;

  return (
    <div className={`tool${refused ? " refused" : ""}${failed ? " failed" : ""}`}>
      <button type="button" className="tool-head" onClick={() => setOpen(!open)}>
        <span className="chev">{open ? "▾" : "▸"}</span>
        <code>{call.name}</code>
        {refused && <span className="tag refused">{String(call.result.refused)}</span>}
        {failed && <span className="tag failed">failed</span>}
        <span className="tool-ms">{Math.round(call.ms)} ms</span>
      </button>
      {open && (
        <div className="tool-body">
          <div>
            <span className="label">asked for</span>
            <pre>{JSON.stringify(call.arguments, null, 2)}</pre>
          </div>
          <div>
            <span className="label">came back</span>
            <pre>
              {JSON.stringify(
                Object.fromEntries(
                  Object.entries(call.result).filter(([k]) => k !== "guidance"),
                ),
                null,
                2,
              )}
            </pre>
          </div>
          {guidance && (
            <div>
              <span className="label">what the agent was told to do</span>
              <p className="guidance">{guidance}</p>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export function CallDetail({ call, onBack }: { call: Detail; onBack: () => void }) {
  // Tool calls are recorded with the caller turn that caused them, so they can
  // be shown underneath it rather than in a separate list nobody correlates.
  const toolsByTurn = new Map<number, RecordedToolCall[]>();
  for (const tool of call.tool_calls_detail) {
    const list = toolsByTurn.get(tool.turn) ?? [];
    list.push(tool);
    toolsByTurn.set(tool.turn, list);
  }

  let callerTurn = 0;
  let agentReplies = 0;

  return (
    <div className="detail">
      <button type="button" className="back" onClick={onBack}>
        ← All calls
      </button>

      <div className="detail-head">
        <h2 className="mono">{call.id}</h2>
        <div className="facts">
          <span>{new Date(call.started_at).toLocaleString()}</span>
          <span>{call.customer_external_id ?? "not identified"}</span>
          <span>{call.verified ? "verified" : "not verified"}</span>
          {call.escalated && <span className="tag escalated">handed off</span>}
          {!call.ended_at && <span className="tag live">did not finish</span>}
        </div>
      </div>

      {call.refusals_detail.length > 0 && (
        <div className="refusal-summary">
          <span className="label">rules that refused something</span>
          {[...new Set(call.refusals_detail)].map((r) => (
            <span key={r} className="tag refused">
              {r}
            </span>
          ))}
        </div>
      )}

      <div className="trace">
        {call.transcript.map((turn, index) => {
          if (turn.speaker === "caller") callerTurn += 1;
          const tools = turn.speaker === "caller" ? toolsByTurn.get(callerTurn) : undefined;
          const timing =
            turn.speaker === "agent" ? call.timings[agentReplies++] : undefined;

          return (
            <div key={index} className={`trace-turn ${turn.speaker}`}>
              <div className="line">
                <span className="who">{turn.speaker === "agent" ? "Wren" : "Caller"}</span>
                <p>{turn.text}</p>
              </div>
              {tools?.map((tool, i) => <ToolCall key={i} call={tool} />)}
              {timing && <TurnTiming timing={timing} />}
            </div>
          );
        })}
      </div>
    </div>
  );
}
