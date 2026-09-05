import type { CallSummary } from "../types";

function when(iso: string): string {
  const at = new Date(iso);
  const minutesAgo = Math.round((Date.now() - at.getTime()) / 60000);
  if (minutesAgo < 1) return "just now";
  if (minutesAgo < 60) return `${minutesAgo}m ago`;
  if (minutesAgo < 60 * 24) return `${Math.round(minutesAgo / 60)}h ago`;
  return at.toLocaleDateString();
}

export function CallList({
  calls,
  onOpen,
  onRefresh,
}: {
  calls: CallSummary[] | null;
  onOpen: (id: string) => void;
  onRefresh: () => void;
}) {
  if (calls === null) return <p className="empty">Loading…</p>;
  if (calls.length === 0)
    return <p className="empty">No calls yet. Start one and it'll show up here.</p>;

  return (
    <>
      <div className="listhead">
        <span>{calls.length} calls</span>
        <button type="button" onClick={onRefresh}>
          Refresh
        </button>
      </div>
      <table className="calls">
        <thead>
          <tr>
            <th>Call</th>
            <th>When</th>
            <th>Household</th>
            <th className="num">Turns</th>
            <th className="num">Tools</th>
            <th className="num">Time</th>
            <th>Outcome</th>
          </tr>
        </thead>
        <tbody>
          {calls.map((call) => (
            <tr key={call.id} onClick={() => onOpen(call.id)}>
              <td className="mono">{call.id.replace("CALL-", "")}</td>
              <td>{when(call.started_at)}</td>
              <td>{call.customer_external_id ?? <span className="muted">not identified</span>}</td>
              <td className="num">{call.turns}</td>
              <td className="num">{call.tool_calls}</td>
              <td className="num">{Math.round(Number(call.total_ms))} ms</td>
              <td>
                {call.escalated && <span className="tag escalated">handed off</span>}
                {call.refusals > 0 && (
                  <span className="tag refused">{call.refusals} refused</span>
                )}
                {call.verified && <span className="tag ok">verified</span>}
                {!call.verified && !call.escalated && call.refusals === 0 && (
                  <span className="muted">—</span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}
