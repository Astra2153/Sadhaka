import { useEffect, useState } from "react";
import { PageHead, Section, Badge, Finding, Empty, Figure, FigureRow } from "@/components/ui";
import { rupees } from "@/lib/format";
import {
  API_BASE, getCredentials, setCredentials, verifyCredentials,
  ledgerRuns, ledgerAdjustments, ledgerAdminLog, postAdjustment, reverseAdjustment,
  type Credentials, type LedgerRun, type Adjustment, type AdminAction, type AdjustmentSummary,
} from "@/lib/api";

/**
 * Admin console.
 *
 * The sign-in here is real: the key is verified by the server on every
 * request, and the server decides the effective role. This page cannot grant
 * itself anything — a forged header without the matching key gets a 403 from
 * the API regardless of what the UI shows. That is the difference between
 * this and a client-side password check, which would be theatre.
 *
 * What the console can do is deliberately narrow: post adjusting entries and
 * read the change log. It cannot edit or delete anything the engine wrote,
 * because the engine's output being immutable is the whole basis for
 * trusting the audit trail.
 */

type Kind = "journal_correction" | "exception_resolution" | "annotation";

const KIND_LABEL: Record<Kind, string> = {
  journal_correction: "Journal correction",
  exception_resolution: "Exception resolution",
  annotation: "Annotation",
};

const KIND_HELP: Record<Kind, string> = {
  journal_correction:
    "A balanced correcting entry posted alongside the original. Must balance to the paise — a correction that does not balance is not a correction, it is a new error.",
  exception_resolution:
    "Record that a flagged variance was investigated and how it was resolved. Does not clear the exception; it annotates it.",
  annotation:
    "A note attached to a run or entity for future reviewers. Carries no financial effect.",
};

export default function Admin() {
  const [creds, setCreds] = useState<Credentials | null>(getCredentials());
  const [roleInput, setRoleInput] = useState<"operator" | "admin">("admin");
  const [keyInput, setKeyInput] = useState("");
  const [signInError, setSignInError] = useState<string | null>(null);
  const [checking, setChecking] = useState(false);

  const [runs, setRuns] = useState<LedgerRun[]>([]);
  const [adjustments, setAdjustments] = useState<Adjustment[]>([]);
  const [summary, setSummary] = useState<AdjustmentSummary | null>(null);
  const [log, setLog] = useState<{ accepted: number; rejected: number; actions: AdminAction[] } | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  // adjustment form
  const [kind, setKind] = useState<Kind>("annotation");
  const [runId, setRunId] = useState("");
  const [reason, setReason] = useState("");
  const [targetRef, setTargetRef] = useState("");
  const [lines, setLines] = useState([
    { account_code: "", account_name: "", debit: "", credit: "", memo: "" },
    { account_code: "", account_name: "", debit: "", credit: "", memo: "" },
  ]);
  const [posting, setPosting] = useState(false);
  const [postResult, setPostResult] = useState<{ ok: boolean; msg: string } | null>(null);

  async function loadAll() {
    setLoadError(null);
    try {
      const [r, a, l] = await Promise.all([
        ledgerRuns(), ledgerAdjustments(), ledgerAdminLog(),
      ]);
      setRuns(r.runs);
      setAdjustments(a.adjustments);
      setSummary(a.summary);
      setLog(l);
      if (!runId && r.runs.length) setRunId(r.runs[0].run_id);
    } catch (e) {
      setLoadError(e instanceof Error ? e.message : String(e));
    }
  }

  useEffect(() => {
    if (creds) loadAll();
  }, [creds]);

  async function signIn() {
    setChecking(true);
    setSignInError(null);
    const candidate: Credentials = { role: roleInput, key: keyInput.trim() };
    const ok = await verifyCredentials(candidate);
    setChecking(false);
    if (!ok) {
      setSignInError(
        "The server rejected that credential. Check that SADHAKA_" +
        roleInput.toUpperCase() + "_KEY is set on the backend and matches " +
        "exactly. With the variable unset, this role is unreachable by design."
      );
      return;
    }
    setCredentials(candidate);
    setCreds(candidate);
    setKeyInput("");
  }

  function signOut() {
    setCredentials(null);
    setCreds(null);
    setRuns([]); setAdjustments([]); setSummary(null); setLog(null);
  }

  function toPaise(v: string): number {
    const n = parseFloat(v);
    return Number.isFinite(n) ? Math.round(n * 100) : 0;
  }

  const lineTotals = lines.reduce(
    (acc, l) => ({ dr: acc.dr + toPaise(l.debit), cr: acc.cr + toPaise(l.credit) }),
    { dr: 0, cr: 0 }
  );
  const balanced = lineTotals.dr === lineTotals.cr && lineTotals.dr > 0;

  async function submit() {
    setPosting(true);
    setPostResult(null);
    try {
      const body: Parameters<typeof postAdjustment>[0] = {
        run_id: runId,
        kind,
        reason,
        targets: targetRef ? { ref: targetRef } : {},
      };
      if (kind === "journal_correction") {
        body.lines = lines
          .filter((l) => l.account_code && (l.debit || l.credit))
          .map((l) => ({
            account_code: l.account_code,
            account_name: l.account_name || l.account_code,
            debit_paise: toPaise(l.debit),
            credit_paise: toPaise(l.credit),
            memo: l.memo,
          }));
      } else {
        body.detail = { note: reason, ref: targetRef };
      }
      const created = await postAdjustment(body);
      setPostResult({ ok: true, msg: `Posted ${created.adjustment_id}.` });
      setReason(""); setTargetRef("");
      setLines([
        { account_code: "", account_name: "", debit: "", credit: "", memo: "" },
        { account_code: "", account_name: "", debit: "", credit: "", memo: "" },
      ]);
      loadAll();
    } catch (e) {
      setPostResult({ ok: false, msg: e instanceof Error ? e.message : String(e) });
    } finally {
      setPosting(false);
    }
  }

  async function doReverse(id: string) {
    const why = window.prompt(
      "Reason for reversing this adjustment (required, and recorded permanently):"
    );
    if (!why || why.trim().length < 10) {
      if (why !== null) window.alert("A reversal needs a substantive reason (10+ characters).");
      return;
    }
    try {
      await reverseAdjustment(id, why.trim());
      loadAll();
    } catch (e) {
      window.alert(e instanceof Error ? e.message : String(e));
    }
  }

  /* ---------------- not signed in ---------------- */
  if (!creds) {
    return (
      <>
        <PageHead
          title="Admin console"
          lede="Post adjusting entries and read the change log."
        />
        <Section
          title="Sign in"
          note="The key is verified by the server on every request, and the server decides the effective role. This page cannot grant itself access — a request with a forged header and no matching key is rejected by the API regardless of what the interface shows."
        >
          {!API_BASE && (
            <Finding title="Live backend required" tone="warn">
              The admin console needs a running API. Set{" "}
              <code className="bg-paper-sunk px-1.5 py-px">VITE_API_BASE_URL</code> and start
              the backend.
            </Finding>
          )}

          <div className="max-w-[440px] space-y-3">
            <div>
              <label className="mb-1 block text-[12.5px] text-ink-soft">Role</label>
              <div className="flex gap-2">
                {(["operator", "admin"] as const).map((r) => (
                  <button
                    key={r}
                    className="chip"
                    aria-pressed={roleInput === r}
                    onClick={() => setRoleInput(r)}
                  >
                    {r}
                  </button>
                ))}
              </div>
            </div>
            <div>
              <label className="mb-1 block text-[12.5px] text-ink-soft">
                Key (matches SADHAKA_{roleInput.toUpperCase()}_KEY on the server)
              </label>
              <input
                className="field w-full"
                type="password"
                value={keyInput}
                onChange={(e) => setKeyInput(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter") signIn(); }}
                placeholder="••••••••"
                disabled={!API_BASE}
              />
            </div>
            <button className="chip" onClick={signIn} disabled={!API_BASE || checking || !keyInput.trim()}>
              {checking ? "Verifying with server…" : "Sign in"}
            </button>
            {signInError && (
              <p className="max-w-[52ch] text-[12.5px] leading-[1.5] text-debit">{signInError}</p>
            )}
          </div>

          <Finding title="What this console can and cannot do" tone="note">
            It can post adjusting entries and read the change log. It{" "}
            <strong>cannot</strong> edit or delete anything the engine wrote — no match
            decision, no confidence score, no exception. The engine's output being
            immutable is the entire basis for trusting the audit trail, so corrections are
            posted alongside the original rather than replacing it, exactly as a
            posted journal entry is reversed rather than erased in real bookkeeping.
          </Finding>
        </Section>
      </>
    );
  }

  /* ---------------- signed in ---------------- */
  return (
    <>
      <PageHead
        title="Admin console"
        lede="Post adjusting entries and read the change log."
        aside={
          <>
            signed in as <Badge tone={creds.role === "admin" ? "debit" : "indigo"}>{creds.role}</Badge>
            <br />
            <button className="mt-1 text-[12px] text-indigo underline" onClick={signOut}>
              sign out
            </button>
          </>
        }
      />

      {loadError && (
        <Finding title="Could not load ledger data" tone="bad">
          {loadError}. If this is a 403, the key is valid for a lower role than this view
          needs.
        </Finding>
      )}

      {summary && (
        <FigureRow>
          <Figure value={summary.total_adjustments} caption="adjustments posted, all time" />
          <Figure value={summary.active} caption="currently in effect" tone="credit" />
          <Figure
            value={summary.reversed}
            caption="reversed — still visible in the ledger"
            tone={summary.reversed ? "amber" : "ink"}
          />
          <Figure value={summary.net_correction} caption="net value of active corrections" />
        </FigureRow>
      )}

      <Section
        title="Post an adjusting entry"
        note="Corrections are append-only. This posts a new entry alongside the original, carrying a required reason and an author. Nothing is overwritten."
      >
        <div className="grid max-w-[820px] grid-cols-1 gap-4 md:grid-cols-2">
          <div>
            <label className="mb-1 block text-[12.5px] text-ink-soft">Kind</label>
            <div className="flex flex-wrap gap-2">
              {(Object.keys(KIND_LABEL) as Kind[]).map((k) => (
                <button key={k} className="chip" aria-pressed={kind === k} onClick={() => setKind(k)}>
                  {KIND_LABEL[k]}
                </button>
              ))}
            </div>
            <p className="mt-2 max-w-[46ch] text-[12px] leading-[1.5] text-ink-soft">
              {KIND_HELP[kind]}
            </p>
          </div>

          <div>
            <label className="mb-1 block text-[12.5px] text-ink-soft">Against run</label>
            <select className="field w-full" value={runId} onChange={(e) => setRunId(e.target.value)}>
              {runs.map((r) => (
                <option key={r.run_id} value={r.run_id}>
                  {r.run_id} {r.has_metrics ? `· ${r.value_match_rate_pct}% value match` : "· no metrics"}
                </option>
              ))}
            </select>
            <label className="mb-1 mt-3 block text-[12.5px] text-ink-soft">
              Target reference (optional — entry id, order id, payment id)
            </label>
            <input
              className="field w-full"
              value={targetRef}
              onChange={(e) => setTargetRef(e.target.value)}
              placeholder="JV-0003 / order_2007 / pay_…"
            />
          </div>
        </div>

        <div className="mt-4 max-w-[820px]">
          <label className="mb-1 block text-[12.5px] text-ink-soft">
            Reason (required, minimum 10 characters — recorded permanently)
          </label>
          <textarea
            className="field w-full"
            rows={2}
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder="Duplicate GST leg identified during month-end review; reversing the second posting."
          />
          <p className="mt-1 text-[11.5px] text-ink-soft">
            An adjustment with no stated reason is indistinguishable from tampering when
            read back later, so the server rejects trivial reasons.
          </p>
        </div>

        {kind === "journal_correction" && (
          <div className="mt-5 max-w-[820px]">
            <h3 className="mb-2 text-[15px] font-semibold">Correcting lines</h3>
            <table className="ledger-table">
              <thead>
                <tr>
                  <th style={{ textAlign: "left" }}>Code</th>
                  <th style={{ textAlign: "left" }}>Account</th>
                  <th>Debit (₹)</th>
                  <th>Credit (₹)</th>
                  <th style={{ textAlign: "left" }}>Memo</th>
                </tr>
              </thead>
              <tbody>
                {lines.map((l, i) => (
                  <tr key={i}>
                    <td style={{ textAlign: "left" }}>
                      <input
                        className="field w-[70px]" value={l.account_code}
                        onChange={(e) => { const n = [...lines]; n[i].account_code = e.target.value; setLines(n); }}
                        placeholder="5900"
                      />
                    </td>
                    <td style={{ textAlign: "left" }}>
                      <input
                        className="field w-full" value={l.account_name}
                        onChange={(e) => { const n = [...lines]; n[i].account_name = e.target.value; setLines(n); }}
                        placeholder="Rounding Differences"
                      />
                    </td>
                    <td>
                      <input
                        className="field w-[90px] text-right" value={l.debit}
                        onChange={(e) => { const n = [...lines]; n[i].debit = e.target.value; setLines(n); }}
                        placeholder="0.00"
                      />
                    </td>
                    <td>
                      <input
                        className="field w-[90px] text-right" value={l.credit}
                        onChange={(e) => { const n = [...lines]; n[i].credit = e.target.value; setLines(n); }}
                        placeholder="0.00"
                      />
                    </td>
                    <td style={{ textAlign: "left" }}>
                      <input
                        className="field w-full" value={l.memo}
                        onChange={(e) => { const n = [...lines]; n[i].memo = e.target.value; setLines(n); }}
                      />
                    </td>
                  </tr>
                ))}
                <tr>
                  <td colSpan={2} style={{ textAlign: "left" }} className="font-semibold">
                    Totals
                  </td>
                  <td className={balanced ? "text-credit" : "text-debit"}>{rupees(lineTotals.dr)}</td>
                  <td className={balanced ? "text-credit" : "text-debit"}>{rupees(lineTotals.cr)}</td>
                  <td style={{ textAlign: "left" }}>
                    {lineTotals.dr === 0 && lineTotals.cr === 0 ? (
                      <span className="text-[12px] text-ink-soft">enter amounts</span>
                    ) : balanced ? (
                      <Badge tone="credit">balances</Badge>
                    ) : (
                      <Badge tone="debit">
                        out by {rupees(Math.abs(lineTotals.dr - lineTotals.cr))}
                      </Badge>
                    )}
                  </td>
                </tr>
              </tbody>
            </table>
            <div className="mt-2 flex gap-2">
              <button
                className="chip"
                onClick={() => setLines([...lines, { account_code: "", account_name: "", debit: "", credit: "", memo: "" }])}
              >
                + line
              </button>
              {lines.length > 2 && (
                <button className="chip" onClick={() => setLines(lines.slice(0, -1))}>
                  − line
                </button>
              )}
            </div>
          </div>
        )}

        <div className="mt-5 flex flex-wrap items-center gap-3">
          <button
            className="chip"
            disabled={
              posting || !runId || reason.trim().length < 10 ||
              (kind === "journal_correction" && !balanced)
            }
            onClick={submit}
          >
            {posting ? "Posting…" : "Post adjustment"}
          </button>
          {postResult && (
            <span className={`text-[13px] ${postResult.ok ? "text-credit" : "text-debit"}`}>
              {postResult.msg}
            </span>
          )}
        </div>
      </Section>

      <Section
        title="Adjustments ledger"
        note="Every adjustment ever posted, including reversed ones. A reversed entry stays visible because the history of what was concluded and later withdrawn is often the most informative part of a ledger."
      >
        {adjustments.length === 0 ? (
          <Empty>No adjustments posted yet.</Empty>
        ) : (
          <table className="ledger-table">
            <thead>
              <tr>
                <th style={{ textAlign: "left" }}>Adjustment</th>
                <th style={{ textAlign: "left" }}>Kind</th>
                <th style={{ textAlign: "left" }}>Reason</th>
                <th>Value</th>
                <th style={{ textAlign: "left" }}>Status</th>
                <th style={{ textAlign: "left" }}></th>
              </tr>
            </thead>
            <tbody>
              {adjustments.map((a) => (
                <tr key={a.adjustment_id}>
                  <td style={{ textAlign: "left" }}>
                    <span className="text-[12.5px]">{a.adjustment_id}</span>
                    <div className="mt-0.5 text-[11px] text-ink-soft">
                      {a.created_at} · {a.author}
                    </div>
                  </td>
                  <td style={{ textAlign: "left", fontFamily: "Inter, sans-serif", fontSize: 12.5 }}>
                    {KIND_LABEL[a.kind as Kind] ?? a.kind}
                  </td>
                  <td style={{ textAlign: "left", fontFamily: "Inter, sans-serif", fontSize: 12.5 }}>
                    <div className="max-w-[46ch] leading-[1.5] text-ink-soft">{a.reason}</div>
                  </td>
                  <td>{a.amount ?? "—"}</td>
                  <td style={{ textAlign: "left" }}>
                    <Badge tone={a.status === "reversed" ? "amber" : "credit"}>{a.status}</Badge>
                    {a.reversed_by && (
                      <div className="mt-0.5 text-[11px] text-ink-soft">by {a.reversed_by}</div>
                    )}
                  </td>
                  <td style={{ textAlign: "left" }}>
                    {a.status === "posted" && creds.role === "admin" && (
                      <button className="text-[12px] text-debit underline" onClick={() => doReverse(a.adjustment_id)}>
                        reverse
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Section>

      <Section
        title="Change log"
        note="Every action taken through this console, accepted or rejected. Rejections are logged too: a change log that records only successes is not an audit log, because the attempts that were refused are exactly what a reviewer needs to see."
      >
        {!log ? (
          <Empty>No log available.</Empty>
        ) : (
          <>
            <div className="mb-4 flex gap-3">
              <Badge tone="credit">{log.accepted} accepted</Badge>
              <Badge tone={log.rejected ? "debit" : "neutral"}>{log.rejected} rejected</Badge>
            </div>
            <table className="ledger-table">
              <thead>
                <tr>
                  <th style={{ textAlign: "left" }}>When</th>
                  <th style={{ textAlign: "left" }}>Action</th>
                  <th style={{ textAlign: "left" }}>Actor</th>
                  <th style={{ textAlign: "left" }}>Target</th>
                  <th style={{ textAlign: "left" }}>Outcome</th>
                  <th style={{ textAlign: "left" }}>Detail</th>
                </tr>
              </thead>
              <tbody>
                {log.actions.map((a) => (
                  <tr key={a.action_id}>
                    <td style={{ textAlign: "left", fontSize: 12 }}>{a.created_at}</td>
                    <td style={{ textAlign: "left", fontFamily: "Inter, sans-serif", fontSize: 12.5 }}>
                      {a.action}
                    </td>
                    <td style={{ textAlign: "left", fontSize: 12 }}>{a.actor_role}</td>
                    <td style={{ textAlign: "left", fontSize: 12 }}>{a.target ?? "—"}</td>
                    <td style={{ textAlign: "left" }}>
                      <span className={a.outcome === "accepted" ? "text-credit" : "text-debit"}>
                        {a.outcome}
                      </span>
                    </td>
                    <td style={{ textAlign: "left", fontFamily: "Inter, sans-serif", fontSize: 12 }}>
                      <div className="max-w-[42ch] text-ink-soft">{a.detail ?? "—"}</div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        )}
      </Section>
    </>
  );
}
