import { useState } from "react";

import scenarios from "../scenarios.json";

/**
 * What to try, for someone who has never seen this before.
 *
 * None of it can be guessed: the phone numbers and passcodes belong to
 * generated households. Without this, trying the agent means having someone
 * next to you reading them out.
 *
 * The three cases are not variations on the same call. Two of them are only
 * interesting if you know what the agent is supposed to refuse to do, so each
 * says what to watch for.
 */

type Scenario = (typeof scenarios)[number];

export function ScriptPanel({ onSay }: { onSay?: (line: string) => void }) {
  const [openId, setOpenId] = useState<string>(scenarios[0].id);
  const [copied, setCopied] = useState<string | null>(null);

  const copy = (value: string) => {
    void navigator.clipboard?.writeText(value);
    setCopied(value);
    setTimeout(() => setCopied(null), 1200);
  };

  return (
    <aside className="scripts">
      <h2>Try one of these</h2>
      <p className="scripts-intro">
        The accounts are made up, so the numbers have to come from here.
      </p>

      {scenarios.map((scenario: Scenario) => {
        const open = scenario.id === openId;
        return (
          <section key={scenario.id} className={`script${open ? " open" : ""}`}>
            <button
              type="button"
              className="script-head"
              onClick={() => setOpenId(open ? "" : scenario.id)}
            >
              <span className="chev">{open ? "▾" : "▸"}</span>
              {scenario.title}
            </button>

            {open && (
              <div className="script-body">
                <p className="script-why">{scenario.why}</p>

                <dl className="credentials">
                  <div>
                    <dt>Phone</dt>
                    <dd>
                      <button type="button" onClick={() => copy(scenario.phone)}>
                        {scenario.phone}
                      </button>
                    </dd>
                  </div>
                  <div>
                    <dt>Passcode</dt>
                    <dd>
                      <button type="button" onClick={() => copy(scenario.passcode)}>
                        {scenario.passcode}
                      </button>
                    </dd>
                  </div>
                </dl>

                <span className="label">Say, in order</span>
                <ol className="lines">
                  {scenario.lines.map((line: string, i: number) => (
                    <li key={i}>
                      <span>{line}</span>
                      {onSay && (
                        <button
                          type="button"
                          className="sayit"
                          onClick={() => onSay(line)}
                          title="Send this as typed text"
                        >
                          send
                        </button>
                      )}
                    </li>
                  ))}
                </ol>

                <span className="label">It should</span>
                <ul className="expect">
                  {scenario.expect.map((item: string, i: number) => (
                    <li key={i}>{item}</li>
                  ))}
                </ul>
              </div>
            )}
          </section>
        );
      })}

      {copied && <p className="copied">Copied {copied}</p>}
    </aside>
  );
}
