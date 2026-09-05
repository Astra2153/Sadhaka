import { useData } from "@/App";
import { PageHead, Section, Line, Figure, FigureRow, Badge, Finding } from "@/components/ui";
import { rupees, pctRaw } from "@/lib/format";

export default function Gst() {
  const { data } = useData();
  const g = data.gst;
  const instruments = Object.entries(g.by_instrument).sort(([a], [b]) => a.localeCompare(b));
  const zeroMdr = instruments.filter(([, b]) => b.contracted_mdr_pct === 0).map(([k]) => k);

  return (
    <>
      <PageHead
        title="GST and input tax credit"
        lede="The stage most reconciliation tools skip, and where Indian merchants quietly lose money."
        aside={
          <>
            {g.invoices.length} monthly invoice{g.invoices.length === 1 ? "" : "s"}
            <br />
            supplier GSTIN {g.invoices[0]?.supplier_gstin ?? "—"}
          </>
        }
      />

      <FigureRow>
        <Figure value={g.display.settled_fee_total} caption="gateway fees charged across all settlements" />
        <Figure value={g.display.settled_tax_total} caption="GST deducted per transaction, inside the settlements" />
        <Figure
          value={g.display.total_itc_claimable}
          caption="input tax credit actually claimable"
          tone={g.total_itc_claimable > 0 ? "credit" : "debit"}
        />
        <Figure
          value={g.display.gst_understated}
          caption="GST understated against the fees charged"
          tone={g.gst_understated > 0 ? "debit" : "ink"}
        />
      </FigureRow>

      <Section
        title="Why this is a separate reconciliation"
        note={
          <>
            GST on the gateway fee is deducted transaction by transaction, inside the
            settlement. But it is <strong>not claimable from the settlement report</strong>.
            Input tax credit can only be claimed against Razorpay's monthly tax invoice,
            and only once that invoice appears in the merchant's GSTR-2B. Two separate
            facts must both hold, and each fails differently.
          </>
        }
      >
        {g.invoices.map((inv) => {
          const blocked = inv.itc_blockers.length > 0;
          return (
            <div key={inv.invoice_no} className="mb-8 border-b border-rule pb-6 last:border-b-0">
              <div className="mb-3 flex flex-wrap items-baseline gap-3">
                <span className="font-serif text-[17px] font-semibold">{inv.invoice_no}</span>
                <span className="text-[13px] text-ink-soft">period {inv.period}</span>
                <Badge tone={blocked ? "debit" : "credit"}>
                  {blocked ? "ITC blocked" : "ITC claimable"}
                </Badge>
                <Badge tone={inv.reflected_in_gstr2b ? "credit" : "amber"}>
                  {inv.reflected_in_gstr2b ? "in GSTR-2B" : "not in GSTR-2B"}
                </Badge>
              </div>

              <div className="grid grid-cols-1 gap-10 md:grid-cols-2">
                <div>
                  <Line label="Taxable value on the invoice" value={rupees(inv.taxable_value)} />
                  <Line label="Tax declared on the invoice" value={rupees(inv.invoice_tax)} />
                  <Line label="Tax summed from settlements" value={rupees(inv.settlement_tax)} />
                  <Line
                    label="Difference"
                    value={rupees(inv.tax_difference)}
                    tone={inv.within_tolerance ? undefined : "debit"}
                    sub={
                      inv.within_tolerance
                        ? "within the monthly tolerance — accumulated per-transaction rounding, book a rounding journal"
                        : "OUTSIDE tolerance — claiming ITC on the invoice while the books carry the settlement figure creates an audit item"
                    }
                    total
                  />
                </div>

                <div>
                  <h3 className="mb-2.5 text-[16px] font-semibold">Eligibility check</h3>
                  <p className="mb-3 max-w-[52ch] text-[13px] text-ink-soft">
                    Under Sec 16 CGST Act read with Sec 16(2)(aa) and Rule 36(4), each
                    condition is checked explicitly and a failure names the specific one.
                  </p>
                  {blocked ? (
                    <ul className="space-y-2">
                      {inv.itc_blockers.map((b, i) => (
                        <li key={i} className="border-l-[3px] border-debit pl-3.5 text-[13px] leading-[1.55] text-ink-soft">
                          {b}
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <p className="border-l-[3px] border-credit pl-3.5 text-[13px] leading-[1.55] text-ink-soft">
                      Valid supplier and recipient GSTINs, invoice reflected in GSTR-2B,
                      and a place of supply consistent with the merchant's registration.
                      {" "}<strong className="text-ink">{rupees(inv.itc_claimable)}</strong> is claimable.
                    </p>
                  )}
                </div>
              </div>
            </div>
          );
        })}
      </Section>

      <Section
        title="Effective rates by instrument"
        note="Recomputed from the settlement data rather than read from it. UPI carrying nil fee and nil GST is correct, not a missing deduction — an engine that expects 2% everywhere flags every UPI row and buries the real exceptions."
      >
        <table className="ledger-table">
          <thead>
            <tr>
              <th>Instrument</th>
              <th>Count</th>
              <th>Gross settled</th>
              <th>Fees</th>
              <th>GST</th>
              <th>Effective MDR</th>
              <th>Contracted</th>
              <th>Effective GST</th>
            </tr>
          </thead>
          <tbody>
            {instruments.map(([name, b]) => {
              const drift = Math.abs(b.effective_mdr_pct - b.contracted_mdr_pct) > 0.005;
              return (
                <tr key={name}>
                  <td className="font-medium">{name}</td>
                  <td>{b.count}</td>
                  <td>{rupees(b.gross)}</td>
                  <td>{rupees(b.fee)}</td>
                  <td>{rupees(b.tax)}</td>
                  <td className={drift ? "text-debit" : ""}>{pctRaw(b.effective_mdr_pct, 3)}</td>
                  <td className="text-ink-soft">{pctRaw(b.contracted_mdr_pct, 2)}</td>
                  <td>{b.fee === 0 ? "—" : pctRaw(b.effective_gst_pct, 2)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>

        {zeroMdr.length > 0 && (
          <Finding title="Zero-MDR instruments are correct, not broken" tone="good">
            {zeroMdr.join(", ")} carries zero MDR by statute (Sec 10A PSS Act, Sec 269SU
            IT Act), so nil fee and nil GST on those rows is the right answer. This is
            modelled explicitly because an engine that assumes a uniform 2% would raise a
            fee variance on every one of those transactions and drown the real findings.
          </Finding>
        )}

        {g.gst_understated > 0 && (
          <Finding title="Understated GST is a cash loss, not a rounding matter" tone="bad">
            Across all settled transactions GST deducted was {g.display.settled_tax_total}, but
            18% of the {g.display.settled_fee_total} of fees charged would be{" "}
            {rupees(g.expected_tax_on_fees)} — understated by {g.display.gst_understated}.
            Every rupee of understated GST is a rupee of input tax credit the merchant
            cannot claim, because credit that was never charged cannot be recovered.
          </Finding>
        )}
      </Section>
    </>
  );
}
