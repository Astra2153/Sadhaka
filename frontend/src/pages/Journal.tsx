import { useState } from "react";
import { useData } from "@/App";
import { PageHead, Section, Figure, FigureRow, Badge, Finding } from "@/components/ui";
import { rupees } from "@/lib/format";

export default function Journal() {
  const { data } = useData();
  const js = data.journalSummary;
  const entries = data.journal;
  const [open, setOpen] = useState<string | null>(null);

  return (
    <>
      <PageHead
        title="Journal entries"
        lede="The postings this reconciliation implies, ready for a ledger."
        aside={
          <>
            {js.entries_balanced} balanced entries
            {js.entries_unbalanced > 0 && <> · {js.entries_unbalanced} rejected</>}
            <br />
            trial balance {js.trial_balances ? "ties" : "OUT OF BALANCE"}
          </>
        }
      />

      <FigureRow>
        <Figure value={js.entries_balanced} caption="balanced entries generated" />
        <Figure value={js.gateway_cost} caption="gateway cost booked as expense" tone="debit" />
        <Figure value={js.gst_recoverable} caption="input GST booked as a recoverable asset" tone="credit" />
        <Figure
          value={js.entries_unbalanced}
          caption="entries rejected for not balancing to the paise"
          tone={js.entries_unbalanced === 0 ? "credit" : "debit"}
        />
      </FigureRow>

      <Section
        title="Why the GST leg is split from the fee"
        note="Reconciliation that stops at the variance leaves someone retyping numbers into Tally. These are the actual postings — and the split matters more than it looks."
      >
        <pre className="max-w-[62ch] border border-rule bg-paper-sunk px-4 py-3.5 font-sans text-[13px] leading-[1.8]">
{`Dr  Bank                        9,764.00
Dr  Payment gateway charges       200.00     (MDR — expense)
Dr  Input GST recoverable          36.00     (18% on MDR — asset)
    Cr  Razorpay clearing                  10,000.00`}
        </pre>
        <Finding title="Booking one line instead of two forfeits the credit" tone="bad">
          Recording Rs 236 as a single expense silently gives up Rs 36 of input tax
          credit. Splitting the GST out as a recoverable asset is the difference
          between claiming it and losing it, and it is invisible in the bank
          statement either way — which is exactly why it gets missed.
        </Finding>
      </Section>

      <Section title="Trial balance" note="Every entry balances individually, and the accounts tie in aggregate. An entry that does not balance to the paise is a bug, so it is rejected rather than exported with a warning.">
        <table className="ledger-table">
          <thead>
            <tr>
              <th>Code</th>
              <th style={{ textAlign: "left" }}>Account</th>
              <th>Type</th>
              <th>Debit</th>
              <th>Credit</th>
              <th>Net</th>
            </tr>
          </thead>
          <tbody>
            {js.trial_balance.map((t) => (
              <tr key={t.account_code}>
                <td className="text-[12px] text-ink-soft">{t.account_code}</td>
                <td style={{ textAlign: "left", fontFamily: "Inter, sans-serif", fontSize: 13 }}>
                  {t.account_name}
                </td>
                <td style={{ fontFamily: "Inter, sans-serif", fontSize: 12 }} className="text-ink-soft">
                  {t.account_type}
                </td>
                <td className={t.debit_paise ? "text-debit" : "text-ink-soft"}>
                  {t.debit_paise ? t.debit : ""}
                </td>
                <td className={t.credit_paise ? "text-credit" : "text-ink-soft"}>
                  {t.credit_paise ? t.credit : ""}
                </td>
                <td>{t.net}</td>
              </tr>
            ))}
            <tr>
              <td />
              <td
                style={{ textAlign: "left", fontFamily: "Inter, sans-serif", fontSize: 13 }}
                className="border-t-[1.5px] border-ink pt-3 font-semibold"
              >
                {js.trial_balances ? "Trial balance ties" : "OUT OF BALANCE"}
              </td>
              <td className="border-t-[1.5px] border-ink" />
              <td className="border-t-[1.5px] border-ink pt-3 font-semibold">{js.trial_debit_total}</td>
              <td className="border-t-[1.5px] border-ink pt-3 font-semibold">{js.trial_credit_total}</td>
              <td className="border-t-[1.5px] border-ink" />
            </tr>
          </tbody>
        </table>
      </Section>

      <Section title="The entries" note="Select one to see its lines. Debits and credits are coloured by side, not by whether they are good news.">
        {entries.map((e) => {
          const isOpen = open === e.entry_id;
          return (
            <div key={e.entry_id} className="border-b border-rule py-3.5">
              <button
                className="flex w-full items-baseline justify-between gap-5 text-left"
                onClick={() => setOpen(isOpen ? null : e.entry_id)}
                aria-expanded={isOpen}
              >
                <span>
                  <span className="figure text-[15px] font-semibold">{e.entry_id}</span>
                  <span className="ml-2 text-[13px] text-ink-soft">{e.date}</span>
                  <Badge>{e.category.replace(/_/g, " ")}</Badge>
                  <span className="mt-1.5 block max-w-[66ch] text-[13px] leading-[1.5] text-ink-soft">
                    {e.narration}
                  </span>
                </span>
                <span className="figure whitespace-nowrap font-semibold">{e.total_debit}</span>
              </button>

              {isOpen && (
                <div className="mt-3 border-l-2 border-rule pl-4.5" style={{ paddingLeft: 18 }}>
                  {e.lines.map((l, i) => (
                    <div
                      key={i}
                      className="grid grid-cols-[58px_1fr_120px_120px] gap-2.5 py-1.5 text-[13px]"
                    >
                      <span className="text-[11.5px] text-ink-soft">{l.account_code}</span>
                      <span>
                        {l.account_name}
                        {l.memo && (
                          <span className="mt-px block text-[12px] text-ink-soft">{l.memo}</span>
                        )}
                      </span>
                      <span className="figure text-right text-debit">{l.debit}</span>
                      <span className="figure text-right text-credit">{l.credit}</span>
                    </div>
                  ))}
                  <div className="mt-1.5 grid grid-cols-[58px_1fr_120px_120px] gap-2.5 border-t border-rule pt-2 text-[13px]">
                    <span />
                    <span className="font-semibold">Totals</span>
                    <span className="figure text-right text-debit">{e.total_debit}</span>
                    <span className="figure text-right text-credit">{e.total_credit}</span>
                  </div>
                </div>
              )}
            </div>
          );
        })}
      </Section>
    </>
  );
}
