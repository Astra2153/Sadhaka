import {
  ComposedChart, Bar, Line as RLine, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, Legend,
} from "recharts";
import { useData } from "@/App";
import { PageHead, Section, Figure, FigureRow, Badge, Finding, Empty } from "@/components/ui";
import { rupees, rupeesShort } from "@/lib/format";

const BAND_TONE = { tight: "credit", moderate: "amber", wide: "debit" } as const;

export default function Forecast() {
  const { data } = useData();
  const f = data.forecast;
  const b = f.behaviour;

  const chart = f.timeline.map((d) => ({
    date: d.date.slice(5),
    weekday: d.weekday,
    expected: d.expected_paise,
    cumulative: d.cumulative_paise,
    items: d.item_count,
  }));

  return (
    <>
      <PageHead
        title="Forward cash position"
        lede="How much lands this week, and how confident the engine is entitled to be about it."
        aside={
          <>
            as of {f.as_of} · {f.horizon_days}-day horizon
            <br />
            settlement cycle contracted at T+{b.contracted_cycle_days}
          </>
        }
      />

      <FigureRow>
        <Figure value={f.expected_total} caption={`expected over the next ${f.horizon_days} days`} tone="credit" />
        <Figure value={f.inflight_net} caption={`${f.inflight_count} order(s) captured, not yet settled`} />
        <Figure value={f.awaiting_credit} caption={`${f.awaiting_credit_count} settlement(s) awaiting bank credit`} />
        <div>
          <Badge tone={BAND_TONE[f.confidence_band]}>{f.confidence_band} confidence band</Badge>
          <div className="mt-2 max-w-[34ch] text-[12.5px] text-ink-soft">{f.confidence_reason}</div>
        </div>
      </FigureRow>

      <Section
        title="What lands, and when"
        note="Bars are the cash expected to land that day. The line is the running total. Only money that already exists is projected — captured orders and created settlements. Future sales are deliberately not forecast."
      >
        <div className="h-[300px] w-full">
          <ResponsiveContainer>
            <ComposedChart data={chart} margin={{ top: 16, right: 12, bottom: 4, left: 4 }}>
              <CartesianGrid vertical={false} />
              <XAxis dataKey="date" tickLine={false} axisLine={{ stroke: "var(--ink)" }} />
              <YAxis tickFormatter={rupeesShort} tickLine={false} axisLine={false} width={62} />
              <Tooltip
                cursor={{ fill: "var(--paper-sunk)" }}
                formatter={(v: number, n) => [rupees(v), n === "expected" ? "landing that day" : "cumulative"]}
              />
              <Legend
                wrapperStyle={{ fontSize: 12, color: "var(--ink-soft)" }}
                formatter={(v) => (v === "expected" ? "expected that day" : "cumulative cash landing")}
              />
              <Bar dataKey="expected" fill="var(--credit)" opacity={0.28} />
              <RLine
                type="monotone"
                dataKey="cumulative"
                stroke="var(--indigo)"
                strokeWidth={2}
                dot={{ r: 3, fill: "var(--indigo)" }}
              />
            </ComposedChart>
          </ResponsiveContainer>
        </div>
      </Section>

      <Section
        title="The lag is learned, not assumed"
        note="The RBI Payment Aggregator Directions of September 2025 replaced the fixed T+1 mandate with a contractually agreed timeline, so what the contract says and what the gateway does can legitimately differ. The forecast follows the behaviour."
      >
        <div className="grid grid-cols-1 gap-10 md:grid-cols-2">
          <div>
            <h3 className="mb-3 text-[16px] font-semibold">Capture to settlement</h3>
            <table className="ledger-table">
              <tbody>
                <tr><td>Observations</td><td>{b.settlement_lag.observations}</td></tr>
                <tr><td>Median</td><td>{b.settlement_lag.median_days} days</td></tr>
                <tr><td>90th percentile</td><td>{b.settlement_lag.p90_days} days</td></tr>
                <tr><td>Standard deviation</td><td>{b.settlement_lag.stdev_days} days</td></tr>
              </tbody>
            </table>
            <p className="mt-2.5 text-[12.5px] text-ink-soft">{b.settlement_lag.source}</p>
          </div>
          <div>
            <h3 className="mb-3 text-[16px] font-semibold">Settlement to bank credit</h3>
            <table className="ledger-table">
              <tbody>
                <tr><td>Observations</td><td>{b.credit_lag.observations}</td></tr>
                <tr><td>Median</td><td>{b.credit_lag.median_days} days</td></tr>
                <tr><td>90th percentile</td><td>{b.credit_lag.p90_days} days</td></tr>
                <tr><td>Standard deviation</td><td>{b.credit_lag.stdev_days} days</td></tr>
              </tbody>
            </table>
            <p className="mt-2.5 text-[12.5px] text-ink-soft">{b.credit_lag.source}</p>
          </div>
        </div>

        {b.drift_note && (
          <Finding title="Observed behaviour differs from the contract" tone="warn">
            {b.drift_note}
          </Finding>
        )}

        <Finding title="What this forecast will not tell you" tone="note">
          It does not forecast future sales. Predicting demand from a short history
          would be a fabricated number wearing a confidence interval, and a finance
          controller acting on it would be worse off than with no forecast at all.
          Amounts on hold are excluded rather than counted as delayed, because that
          money is not scheduled to land.
        </Finding>
      </Section>

      <Section title="At risk" note="Money that exists but is not on the timeline, and why.">
        {f.at_risk.length === 0 ? (
          <Empty>Nothing flagged as at risk in this run.</Empty>
        ) : (
          f.at_risk.map((r) => (
            <div key={r.category} className="finding finding-bad my-4">
              <h4 className="mb-1.5">
                <span className="figure text-[17px]">{r.amount}</span>{" "}
                <span className="font-sans text-[13px] font-normal text-ink-soft">
                  · {r.category.replace(/_/g, " ").toLowerCase()} · {r.count} item{r.count === 1 ? "" : "s"}
                </span>
              </h4>
              <p className="text-[13.5px] leading-[1.6] text-ink-soft">{r.note}</p>
            </div>
          ))
        )}
      </Section>

      {f.inflight_detail.length > 0 && (
        <Section title="In flight" note="Captured orders that have not yet appeared in a settlement, largest first.">
          <table className="ledger-table">
            <thead>
              <tr>
                <th>Order</th><th>Method</th><th>Gross</th><th>Fee</th><th>GST</th>
                <th>Net expected</th><th>Expected date</th><th>Late case</th>
              </tr>
            </thead>
            <tbody>
              {f.inflight_detail.slice(0, 12).map((o) => (
                <tr key={String(o.order_id)}>
                  <td className="font-medium">{String(o.order_id)}</td>
                  <td>{String(o.method)}</td>
                  <td>{rupees(Number(o.gross))}</td>
                  <td>{rupees(Number(o.fee))}</td>
                  <td>{rupees(Number(o.tax))}</td>
                  <td>{String(o.net_display)}</td>
                  <td>{String(o.expected_date)}</td>
                  <td className="text-ink-soft">{String(o.late_date)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Section>
      )}
    </>
  );
}
