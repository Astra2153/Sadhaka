import { useEffect, useState } from "react";
import {
  LineChart, Line as RLine, AreaChart, Area, BarChart, Bar,
  XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Legend, Cell,
} from "recharts";
import { PageHead, Section, Figure, FigureRow, Empty, Badge, Finding, Loading } from "@/components/ui";
import { rupees, rupeesShort } from "@/lib/format";
import { useData } from "@/App";
import {
  API_BASE, ledgerRuns, ledgerTrend, ledgerAdjustments,
  type LedgerRun, type TrendPoint, type Adjustment, type AdjustmentSummary,
} from "@/lib/api";

/**
 * The ledger's time dimension.
 *
 * Every other page in this app answers "how did THIS run go". This one asks
 * the question a finance controller actually has after the second month:
 * is reconciliation quality improving, holding, or quietly degrading? A
 * single run's match rate cannot answer that. The trend can.
 */

export default function Ledger() {
  const { data } = useData();
  const [runs, setRuns] = useState<LedgerRun[] | null>(null);
  const [trend, setTrend] = useState<TrendPoint[] | null>(null);
  const [adjustments, setAdjustments] = useState<Adjustment[]>([]);
  const [summary, setSummary] = useState<AdjustmentSummary | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!API_BASE) { setError("offline"); return; }
    Promise.all([ledgerRuns(), ledgerTrend(), ledgerAdjustments()])
      .then(([r, t, a]) => {
        setRuns(r.runs);
        setTrend(t.points);
        setAdjustments(a.adjustments);
        setSummary(a.summary);
      })
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, []);

  /* Money composition of the current run — where the settled gross went.
     Built from the loaded bundle so this renders even offline. */
  const m = data.summary.metrics;
  const parsePaise = (s?: string) => {
    if (!s) return 0;
    const n = parseFloat(s.replace(/[^0-9.-]/g, ""));
    return Number.isFinite(n) ? Math.round(n * 100) : 0;
  };
  const gross = parsePaise(m.money.total_settled_gross);
  const fees = parsePaise(m.money.total_fees_charged);
  const gst = parsePaise(m.money.total_gst_on_fees);
  const banked = parsePaise(m.money.total_banked);
  const otherDeductions = Math.max(0, gross - fees - gst - banked);

  const composition = [
    { name: "Reached the bank", value: banked, tone: "var(--credit)" },
    { name: "Gateway fees (MDR)", value: fees, tone: "var(--debit)" },
    { name: "GST on fees", value: gst, tone: "var(--amber)" },
    ...(otherDeductions > 0
      ? [{ name: "Refunds, disputes, holds", value: otherDeductions, tone: "var(--ink-soft)" }]
      : []),
  ];

  const trendChart = (trend ?? []).map((p, i) => ({
    label: `#${i + 1}`,
    run_id: p.run_id,
    value: p.value_match_rate_pct,
    batch: p.batch_match_rate_pct,
    order: p.order_match_rate_pct,
    actionable: p.exceptions_actionable ?? 0,
    benign: p.exceptions_benign ?? 0,
  }));

  return (
    <>
      <PageHead
        title="Ledger"
        lede="Every run ever recorded, and how reconciliation quality has moved across them."
        aside={
          runs ? (
            <>
              {runs.length} run{runs.length === 1 ? "" : "s"} on record
              <br />
              {summary ? `${summary.total_adjustments} adjustment(s) posted` : ""}
            </>
          ) : null
        }
      />

      {error === "offline" ? (
        <Finding title="Live backend required for run history" tone="warn">
          The ledger reads every historical run from the audit database, which only
          exists on a running backend. Set{" "}
          <code className="bg-paper-sunk px-1.5 py-px">VITE_API_BASE_URL</code> to see it.
          The money composition below still renders from the bundled run.
        </Finding>
      ) : error ? (
        <Finding title="Could not load run history" tone="bad">{error}</Finding>
      ) : null}

      <Section
        title="Where the settled money went"
        note="Composition of this run's settled gross. The gap between what was sold and what reached the bank is a cost of service plus a recoverable tax — not a discount, and not a loss."
      >
        <div className="h-[260px] w-full max-w-[760px]">
          <ResponsiveContainer>
            <BarChart
              data={composition.map((c) => ({ ...c }))}
              layout="vertical"
              margin={{ top: 4, right: 90, bottom: 4, left: 130 }}
            >
              <CartesianGrid horizontal={false} />
              <XAxis type="number" tickFormatter={rupeesShort} tickLine={false} axisLine={false} />
              <YAxis
                type="category" dataKey="name" width={160}
                tickLine={false} axisLine={{ stroke: "var(--ink)" }}
              />
              <Tooltip
                cursor={{ fill: "var(--paper-sunk)" }}
                formatter={(v: number) => [rupees(v), ""]}
              />
              <Bar dataKey="value">
                {composition.map((c, i) => (
                  <Cell key={i} fill={c.tone} opacity={0.82} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>
        <p className="mt-3 max-w-[74ch] text-[12.5px] text-ink-soft">
          Settled gross {m.money.total_settled_gross} → banked {m.money.total_banked}. The
          difference is {rupees(fees + gst + otherDeductions)}, of which{" "}
          {m.money.total_gst_on_fees} is GST the merchant can reclaim as input tax credit
          provided it stays itemised separately from the fee.
        </p>
      </Section>

      {!runs && !error && <Loading what="run history" />}

      {trendChart.length > 1 && (
        <>
          <Section
            title="Match rate across runs"
            note="A single run's match rate says nothing about whether reconciliation quality is improving or degrading. This does. Oldest run on the left."
          >
            <div className="h-[300px] w-full">
              <ResponsiveContainer>
                <LineChart data={trendChart} margin={{ top: 16, right: 16, bottom: 4, left: 0 }}>
                  <CartesianGrid vertical={false} />
                  <XAxis dataKey="label" tickLine={false} axisLine={{ stroke: "var(--ink)" }} />
                  <YAxis
                    domain={[90, 100]} tickFormatter={(v) => `${v}%`}
                    tickLine={false} axisLine={false} width={48}
                  />
                  <Tooltip
                    formatter={(v: number, n) => [`${v?.toFixed?.(2) ?? v}%`, String(n)]}
                    labelFormatter={(l) => {
                      const p = trendChart.find((x) => x.label === l);
                      return p?.run_id ?? String(l);
                    }}
                  />
                  <Legend wrapperStyle={{ fontSize: 12, color: "var(--ink-soft)" }} />
                  <RLine type="monotone" dataKey="value" name="by settled value"
                         stroke="var(--indigo)" strokeWidth={2} dot={{ r: 3 }} connectNulls />
                  <RLine type="monotone" dataKey="batch" name="bank to batch"
                         stroke="var(--credit)" strokeWidth={1.6} dot={{ r: 2.4 }} connectNulls />
                  <RLine type="monotone" dataKey="order" name="txn to order"
                         stroke="var(--amber)" strokeWidth={1.6} dot={{ r: 2.4 }} connectNulls />
                </LineChart>
              </ResponsiveContainer>
            </div>
          </Section>

          <Section
            title="Exception load across runs"
            note="Split benign from actionable. Total exception count rising is not automatically bad — what matters is whether the share needing a human is growing."
          >
            <div className="h-[280px] w-full">
              <ResponsiveContainer>
                <AreaChart data={trendChart} margin={{ top: 16, right: 16, bottom: 4, left: 0 }}>
                  <CartesianGrid vertical={false} />
                  <XAxis dataKey="label" tickLine={false} axisLine={{ stroke: "var(--ink)" }} />
                  <YAxis tickLine={false} axisLine={false} width={40} />
                  <Tooltip
                    labelFormatter={(l) => {
                      const p = trendChart.find((x) => x.label === l);
                      return p?.run_id ?? String(l);
                    }}
                  />
                  <Legend wrapperStyle={{ fontSize: 12, color: "var(--ink-soft)" }} />
                  <Area type="monotone" dataKey="benign" name="explained" stackId="1"
                        stroke="var(--ink-soft)" fill="var(--ink-soft)" fillOpacity={0.22} />
                  <Area type="monotone" dataKey="actionable" name="needs action" stackId="1"
                        stroke="var(--debit)" fill="var(--debit)" fillOpacity={0.5} />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          </Section>
        </>
      )}

      {runs && runs.length > 0 && (
        <Section
          title="Run history"
          note="Every reconciliation the engine has performed. Runs are never overwritten, so this is the complete record."
        >
          <table className="ledger-table">
            <thead>
              <tr>
                <th style={{ textAlign: "left" }}>Run</th>
                <th>Records</th>
                <th>Decisions</th>
                <th>Value match</th>
                <th>Exceptions</th>
                <th style={{ textAlign: "left" }}>Notes</th>
              </tr>
            </thead>
            <tbody>
              {runs.map((r) => (
                <tr key={r.run_id}>
                  <td style={{ textAlign: "left" }}>
                    <span className="text-[12.5px]">{r.run_id}</span>
                    <div className="mt-0.5 text-[11px] text-ink-soft">{r.started_at}</div>
                  </td>
                  <td>{r.records ?? "—"}</td>
                  <td>{r.decision_count}</td>
                  <td className={r.value_match_rate_pct == null ? "text-ink-soft" : ""}>
                    {r.value_match_rate_pct != null ? `${r.value_match_rate_pct}%` : "—"}
                  </td>
                  <td>
                    {r.exceptions_total != null ? (
                      <>
                        {r.exceptions_total}
                        <span className="text-[11px] text-ink-soft">
                          {" "}({r.exceptions_actionable} act.)
                        </span>
                      </>
                    ) : "—"}
                  </td>
                  <td style={{ textAlign: "left", fontFamily: "Inter, sans-serif", fontSize: 12 }}>
                    {r.has_metrics ? (
                      <span className="text-ink-soft">{r.notes ?? "—"}</span>
                    ) : (
                      <Badge tone="amber">no metrics stored</Badge>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Section>
      )}

      {summary && summary.total_adjustments > 0 && (
        <Section
          title="Adjusting entries"
          note={summary.note}
        >
          <FigureRow>
            <Figure value={summary.active} caption="corrections currently in effect" tone="credit" />
            <Figure value={summary.reversed} caption="reversed, still on record"
                    tone={summary.reversed ? "amber" : "ink"} />
            <Figure value={summary.net_correction} caption="net value of active corrections" />
          </FigureRow>

          <table className="ledger-table">
            <thead>
              <tr>
                <th style={{ textAlign: "left" }}>Adjustment</th>
                <th style={{ textAlign: "left" }}>Kind</th>
                <th style={{ textAlign: "left" }}>Reason</th>
                <th>Value</th>
                <th style={{ textAlign: "left" }}>Status</th>
              </tr>
            </thead>
            <tbody>
              {adjustments.map((a) => (
                <tr key={a.adjustment_id}>
                  <td style={{ textAlign: "left" }}>
                    <span className="text-[12.5px]">{a.adjustment_id}</span>
                    <div className="mt-0.5 text-[11px] text-ink-soft">{a.created_at}</div>
                  </td>
                  <td style={{ textAlign: "left", fontFamily: "Inter, sans-serif", fontSize: 12.5 }}>
                    {a.kind.replace(/_/g, " ")}
                  </td>
                  <td style={{ textAlign: "left", fontFamily: "Inter, sans-serif", fontSize: 12.5 }}>
                    <div className="max-w-[48ch] leading-[1.5] text-ink-soft">{a.reason}</div>
                  </td>
                  <td>{a.amount ?? "—"}</td>
                  <td style={{ textAlign: "left" }}>
                    <Badge tone={a.status === "reversed" ? "amber" : "credit"}>{a.status}</Badge>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Section>
      )}

      {runs && runs.length === 1 && (
        <Finding title="Only one run on record" tone="note">
          Trend charts need at least two runs to say anything. Run the pipeline again
          (<code className="bg-paper-sunk px-1.5 py-px">python src/run_pipeline.py</code>)
          and reload — each run is recorded separately, so the history builds up on its own.
        </Finding>
      )}
    </>
  );
}
