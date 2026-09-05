import { Link } from "react-router-dom";
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Cell,
} from "recharts";
import { useData } from "@/App";
import { PageHead, Section, RateRow, Line, Figure, Badge, Finding } from "@/components/ui";
import { rupees } from "@/lib/format";
import { API_BASE } from "@/lib/api";

export default function Overview() {
  const { data } = useData();
  const m = data.summary.metrics;
  const r = m.match_rates;
  const sc = data.scorecard;

  const confidence = Object.entries(m.confidence_distribution).map(([band, n]) => ({
    band: band === "below-0.65" ? "below 65%" : band.replace("0.", "").replace("-", "–"),
    n,
    below: band === "below-0.65",
  }));

  return (
    <>
      <PageHead
        title="Sadhaka"
        lede="Reconciles what Razorpay settled against what the bank paid and what the merchant sold — and explains every rupee it cannot place."
        aside={
          <>
            run <code className="rounded-[2px] bg-paper-sunk px-1.5 py-px">{data.summary.run_id}</code>
            <br />
            {m.throughput.total_records_processed} records · {m.throughput.settlement_batches} settlement batches
            <br />
            {data.config.merchant.legal_name} · GSTIN {data.config.merchant.gstin}
            <br />
            {API_BASE ? (
              <a
                href={`${API_BASE}/report/pdf`}
                className="mt-1 inline-block border-b border-rule-strong text-indigo hover:border-indigo"
              >
                ↓ Download audit-ready PDF
              </a>
            ) : (
              <span className="mt-1 inline-block text-ink-soft" title="Needs a live backend">
                PDF export needs a live API
              </span>
            )}
          </>
        }
      />

      {/* The tally: two sides that must agree, with the reconciled share as
          the bridge between them. This is the ledger metaphor made literal. */}
      <div className="grid grid-cols-1 gap-6 border-b border-rule py-8 md:grid-cols-[1fr_auto_1fr] md:gap-0">
        <div className="md:px-2">
          <div className="mb-2 text-[12px] text-ink-soft">Credited by the bank</div>
          <div className="figure text-[36px] leading-none">{m.money.total_banked}</div>
          <div className="mt-1.5 text-[13px] text-ink-soft">
            across {m.throughput.bank_credits} NEFT credits
          </div>
        </div>

        <div className="flex min-w-[180px] flex-col items-center justify-center border-y border-rule py-4 md:border-x md:border-y-0 md:px-8 md:py-0">
          <div className="figure text-[46px] font-semibold leading-none text-credit">
            {r.value_match_rate_pct.toFixed(1)}%
          </div>
          <div className="mt-2 max-w-[15ch] text-center text-[11.5px] text-ink-soft">
            of settled value verified clean
          </div>
        </div>

        <div className="md:px-2 md:text-right">
          <div className="mb-2 text-[12px] text-ink-soft">Settled gross by Razorpay</div>
          <div className="figure text-[36px] leading-none">{m.money.total_settled_gross}</div>
          <div className="mt-1.5 text-[13px] text-ink-soft">
            across {m.throughput.settled_transactions} transactions, before fees and GST
          </div>
        </div>
      </div>

      <Section
        title="Match rates"
        note="Four rates, each with its denominator stated. A single headline percentage hides which records it counted — the value rate matters most, because one large exception can hide behind ninety small clean rows."
      >
        <RateRow label="Bank credit to batch" pct={r.batch_match_rate_pct} denominator={r.batch_match_denominator} />
        <RateRow label="Transaction to order" pct={r.order_match_rate_pct} denominator={r.order_match_denominator} />
        <RateRow label="By settled value" pct={r.value_match_rate_pct} denominator={r.value_match_denominator} />
        <RateRow label="By banked value" pct={r.bank_value_match_rate_pct} denominator={r.bank_value_denominator} />
      </Section>

      <Section
        title="Where the money went"
        note="The gap between what was sold and what reached the bank is not a discount. It is a cost of service plus a recoverable tax, and the two are booked separately."
      >
        <div className="grid grid-cols-1 gap-11 md:grid-cols-2">
          <div>
            <Line label="Banked" value={m.money.total_banked} tone="credit" />
            <Line label="Settled gross" value={m.money.total_settled_gross} />
            <Line label="Fees charged (MDR)" value={m.money.total_fees_charged} tone="debit" />
            <Line label="GST on fees" value={m.money.total_gst_on_fees} tone="debit" />
            <Line
              label="Input tax credit claimable"
              value={m.money.itc_claimable}
              tone="credit"
              sub="claimable only against the monthly invoice, once reflected in GSTR-2B"
              total
            />
          </div>

          <div>
            <h3 className="mb-3 text-[17px] font-semibold">Exceptions</h3>
            <p className="mb-4 max-w-[46ch] text-[13px] text-ink-soft">
              Split into benign and actionable. Reporting {m.exceptions.total} exceptions
              when {m.exceptions.benign} of them are timing lags and reserve holds
              would be alarmism.
            </p>
            <Line
              label="Needs a human"
              value={m.exceptions.actionable_value}
              tone="debit"
              sub={`${m.exceptions.actionable} exception${m.exceptions.actionable === 1 ? "" : "s"}`}
            />
            <Line
              label="Explained, no action"
              value={m.exceptions.benign_value}
              sub={`${m.exceptions.benign} exception${m.exceptions.benign === 1 ? "" : "s"}`}
            />
            <p className="mt-5 text-[13px]">
              <Link to="/exceptions" className="border-b border-rule-strong text-indigo hover:border-indigo">
                Open the exception ledger →
              </Link>
            </p>
          </div>
        </div>
      </Section>

      <Section
        title="Match confidence"
        note={`Every match carries a confidence. Anything below ${data.config.auto_accept_threshold} is held for review rather than auto-accepted, because a coin flip on money is worse than an honest exception.`}
      >
        <div className="h-[220px] w-full max-w-[640px]">
          <ResponsiveContainer>
            <BarChart data={confidence} margin={{ top: 16, right: 8, bottom: 4, left: -8 }}>
              <CartesianGrid vertical={false} />
              <XAxis dataKey="band" tickLine={false} axisLine={{ stroke: "var(--ink)" }} />
              <YAxis tickLine={false} axisLine={false} width={40} />
              <Tooltip cursor={{ fill: "var(--paper-sunk)" }} formatter={(v) => [`${v} matches`, ""]} />
              <Bar dataKey="n" radius={0}>
                {confidence.map((c, i) => (
                  <Cell key={i} fill={c.below ? "var(--debit)" : "var(--indigo)"} opacity={0.78} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>
        <p className="mt-3 max-w-[70ch] text-[12.5px] text-ink-soft">
          {(m.confidence_distribution["below-0.65"] ?? 0) === 0
            ? "No match was accepted below the threshold in this run."
            : `${m.confidence_distribution["below-0.65"]} match(es) fell below the threshold and were held for review.`}
        </p>
      </Section>

      <Section
        title="Self-score against the answer key"
        note="The dataset generator plants known faults and known traps. Faults are caught by raising the right exception; traps are passed by not producing a wrong match. They are scored separately, because conflating them would let the engine look good for the wrong reason."
      >
        <div className="flex flex-wrap gap-x-11 gap-y-6">
          <Figure
            value={`${sc.detected}/${sc.planted_faults}`}
            caption={`planted faults detected — ${sc.recall_pct}% recall`}
            tone={sc.recall_pct === 100 ? "credit" : "amber"}
          />
          <Figure
            value={`${sc.traps_passed}/${sc.planted_traps}`}
            caption="traps avoided by not producing a wrong match"
            tone={sc.trap_pass_pct === 100 ? "credit" : "debit"}
          />
          <Figure
            value={`${sc.code_accuracy_pct}%`}
            caption="classified with the expected variance code"
          />
        </div>

        <div className="mt-7 grid grid-cols-1 gap-10 md:grid-cols-2">
          <div>
            <h3 className="mb-3 text-[16px] font-semibold">Faults to detect</h3>
            {sc.cases.map((c) => (
              <div key={c.id} className="grid grid-cols-[62px_1fr] gap-3.5 border-b border-dotted border-rule py-2.5 text-[13.5px] last:border-b-0">
                <span className={`pt-0.5 text-[11.5px] font-semibold ${c.detected ? "text-credit" : "text-debit"}`}>
                  {c.detected ? "FOUND" : "MISSED"}
                </span>
                <span>
                  <strong className="font-normal">{c.type.replace(/_/g, " ").toLowerCase()}</strong>
                  {" · "}
                  <span className="text-ink-soft">{c.expected_code}</span>
                  {!c.detected && (
                    <em className="mt-1 block text-[12.5px] text-ink-soft">{c.detail}</em>
                  )}
                </span>
              </div>
            ))}
          </div>
          <div>
            <h3 className="mb-3 text-[16px] font-semibold">Traps to avoid</h3>
            {sc.traps.map((t) => (
              <div key={t.id} className="grid grid-cols-[62px_1fr] gap-3.5 border-b border-dotted border-rule py-2.5 text-[13.5px] last:border-b-0">
                <span className={`pt-0.5 text-[11.5px] font-semibold ${t.passed ? "text-credit" : "text-debit"}`}>
                  {t.passed ? "AVOIDED" : "FELL IN"}
                </span>
                <span>
                  <strong className="font-normal">{t.type.replace(/_/g, " ").toLowerCase()}</strong>
                  <em className="mt-1 block text-[12.5px] text-ink-soft">{t.detail}</em>
                </span>
              </div>
            ))}
          </div>
        </div>

        <Finding title="A perfect score here proves less than it looks" tone="note">
          These faults were planted by the same author who wrote the engine. That is
          a demonstration, not evidence. The{" "}
          <Link to="/verification" className="border-b border-rule-strong text-indigo hover:border-indigo">
            verification report
          </Link>{" "}
          attacks the engine programmatically instead, with {" "}
          {(1975).toLocaleString()} injected faults, and reports the blind spots it found.
        </Finding>
      </Section>

      <Section title="Configuration this run ran with" note="A match rate is not interpretable without the tolerances that produced it, so they are published rather than buried.">
        <div className="flex flex-wrap gap-2">
          {Object.entries(data.config.tolerances).map(([k, v]) => (
            <Badge key={k}>{k.replace(/_/g, " ")}: {String(v)}</Badge>
          ))}
          <Badge tone="indigo">auto-accept ≥ {data.config.auto_accept_threshold}</Badge>
          <Badge>settlement cycle {data.config.merchant.settlement_cycle}</Badge>
          <Badge>GST on MDR {data.config.gst_on_mdr_pct}%</Badge>
        </div>
        <ul className="mt-5 max-w-[80ch] space-y-2 text-[13px] text-ink-soft">
          {Object.entries(data.config.statutory_notes).map(([k, v]) => (
            <li key={k}>
              <strong className="font-medium text-ink">{k.replace(/_/g, " ")}</strong> — {v}
            </li>
          ))}
        </ul>
      </Section>
    </>
  );
}
