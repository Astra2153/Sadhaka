import { useData } from "@/App";
import { PageHead, Section, Figure, FigureRow, Line, Finding, Badge } from "@/components/ui";

export default function Marketplace() {
  const { data } = useData();
  const m = data.marketplace;
  const r = m.rates_applied;

  return (
    <>
      <PageHead
        title="Marketplace scenario"
        lede="Where Section 194-O TDS and Section 52 GST TCS actually apply — and why they are absent everywhere else."
        aside={
          <>
            as of {m.as_of}
            <br />
            {m.payout_count} payouts to {m.seller_count} sellers
          </>
        }
      />

      <Section
        title="Why this is a separate scenario"
        note="The main pipeline models a merchant selling its own goods through a payment aggregator. In that model neither deduction applies, and modelling them would generate confident, wrong exceptions on every row. Here the platform pays third-party sellers through Route-style split payouts, so both can attach."
      >
        <div className="flex flex-wrap gap-2">
          <Badge tone="indigo">194-O standard {Number(r.tds_194o_standard_pct).toFixed(2)}%</Badge>
          <Badge tone="debit">194-O without PAN {Number(r.tds_194o_no_pan_pct).toFixed(2)}%</Badge>
          <Badge>Individual/HUF threshold {String(r.individual_huf_threshold)}</Badge>
          <Badge tone="indigo">Section 52 TCS {Number(r.tcs_52_pct).toFixed(2)}%</Badge>
        </div>
        <p className="mt-4 max-w-[80ch] text-[13px] text-ink-soft">
          Rates are resolved from effective-dated bands, so a notification change is a
          configuration edit rather than a code change. Section 194-O moved from 1% to
          0.1% on 1 October 2024; Section 52 TCS was halved to 0.5% on 10 July 2024.
        </p>
      </Section>

      <FigureRow>
        <Figure value={m.gross} caption="gross value routed to third-party sellers" />
        <Figure value={m.commission} caption="platform commission retained" tone="credit" />
        <Figure value={m.tds_194o_deducted} caption="Section 194-O TDS deducted at payout" tone="debit" />
        <Figure value={m.tcs_52_collected} caption="Section 52 GST TCS collected" tone="debit" />
      </FigureRow>

      <Section
        title="The reconciliation problem statutory deductions create"
        note="Both are deducted at payout, but neither becomes visible to the seller immediately. TDS reaches Form 26AS only after the deductor files the quarterly return and the challan is processed. TCS reaches GSTR-8 on the operator's filing. So a correctly deducted amount looks like an unexplained shortfall to the seller for weeks."
      >
        <div className="grid grid-cols-1 gap-10 md:grid-cols-2">
          <div>
            <h3 className="mb-3 text-[16px] font-semibold">TDS visibility</h3>
            <Line label="Deducted at payout" value={m.tds_194o_deducted} tone="debit" />
            <Line label="Reflected in Form 26AS" value={m.tds_194o_in_26as} tone="credit" />
            <Line
              label="Deducted but not yet claimable"
              value={m.tds_not_yet_visible}
              tone="debit"
              sub="the seller cannot claim credit their 26AS does not yet show"
              total
            />
          </div>
          <div>
            <h3 className="mb-3 text-[16px] font-semibold">Status of each deduction</h3>
            <Line label="Matched to 26AS" value={String(m.tds_matched)} tone="credit" />
            <Line
              label="Pending, within tolerance"
              value={String(m.tds_pending_within_tolerance)}
              sub="expected — the statement has not caught up yet, which is benign"
            />
            <Line
              label="Overdue"
              value={String(m.tds_overdue)}
              tone={m.tds_overdue > 0 ? "debit" : undefined}
              sub="beyond the lag tolerance; verify the deductor filed with the right PAN and quarter"
            />
            <Line label="Net paid to sellers" value={m.net_paid} total />
          </div>
        </div>

        <Finding title="Modelling the lag is the point" tone="note">
          An engine that does not model statement lag will report a legitimate deduction
          as missing money every single quarter, train its users to ignore the alert, and
          then miss the quarter when something is genuinely wrong. Classifying the wait as
          a timing difference — and stating how many days remain before it stops being
          benign — is the difference between a useful alert and noise.
        </Finding>
      </Section>

      <Section title="Payouts" note="Each seller's applicable rules resolved individually, because entity type, PAN status and GSTIN registration each change the answer.">
        <table className="ledger-table">
          <thead>
            <tr>
              <th style={{ textAlign: "left" }}>Payout</th>
              <th style={{ textAlign: "left" }}>Seller</th>
              <th style={{ textAlign: "left" }}>Type</th>
              <th>Gross</th><th>TDS</th><th>TCS</th><th>Net paid</th>
            </tr>
          </thead>
          <tbody>
            {m.payouts.slice(0, 16).map((p) => (
              <tr key={String(p.payout_id)}>
                <td style={{ textAlign: "left" }}>{String(p.payout_id)}</td>
                <td style={{ textAlign: "left", fontFamily: "Inter, sans-serif", fontSize: 12.5 }}>
                  {String(p.seller_name)}
                </td>
                <td style={{ textAlign: "left", fontFamily: "Inter, sans-serif", fontSize: 12 }} className="text-ink-soft">
                  {String(p.entity_type)}
                </td>
                <td>{String(p.gross)}</td>
                <td className={Number(p.tds_194o_paise) > 0 ? "text-debit" : "text-ink-soft"}>
                  {String(p.tds)}
                </td>
                <td className={Number(p.tcs_52_paise) > 0 ? "text-debit" : "text-ink-soft"}>
                  {String(p.tcs)}
                </td>
                <td>{String(p.net_paid)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="mt-3 max-w-[76ch] text-[12.5px] text-ink-soft">
          A nil TDS row is not an omission. A resident individual or HUF with PAN on file
          and projected annual gross below {String(r.individual_huf_threshold)} is exempt
          under Section 194-O, while companies, firms and LLPs are deducted from the first
          rupee. The engine records the reasoning per payout rather than the amount alone.
        </p>
      </Section>
    </>
  );
}
