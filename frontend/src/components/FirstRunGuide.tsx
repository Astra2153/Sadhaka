import { useState } from "react";

/**
 * First-run orientation.
 *
 * Written for someone who has never seen this project, may not know what a
 * settlement or an MDR is, and has perhaps ten seconds of patience before
 * deciding whether to keep reading. It is collapsible and remembers nothing —
 * no storage, no cookie — because a banner that cannot be permanently
 * dismissed is annoying, and one that silently never returns is worse for a
 * reviewer who wants to find it again.
 */

const GLOSSARY: { term: string; plain: string }[] = [
  {
    term: "Settlement",
    plain:
      "One lump payout from Razorpay to the merchant's bank, covering many customer orders at once — net of fees, taxes, refunds and disputes.",
  },
  {
    term: "MDR",
    plain:
      "Merchant Discount Rate: the gateway's fee, charged as a percentage of each transaction. 2% on cards here. UPI is zero by law.",
  },
  {
    term: "GST on MDR",
    plain:
      "18% tax charged on the fee itself, never on the sale value. The merchant can reclaim it as input tax credit — but only if it is itemised separately.",
  },
  {
    term: "ITC",
    plain:
      "Input Tax Credit: GST the merchant already paid and can deduct from GST they owe. Lose the paperwork, lose the money.",
  },
  {
    term: "UTR",
    plain:
      "The bank's reference number for a transfer. Issued by the bank, not by Razorpay — which is exactly why it drifts and can't be trusted as the only join key.",
  },
  {
    term: "Reconciliation",
    plain:
      "Proving that what was sold, what the gateway says it settled, and what the bank actually paid all agree — and explaining every rupee where they don't.",
  },
  {
    term: "Paise",
    plain:
      "1/100 of a rupee. All money here is stored as whole paise, never decimals, because floating point cannot represent 0.01 exactly.",
  },
];

const READING_ORDER: { page: string; question: string }[] = [
  { page: "Overview", question: "Did the money tie out, and how much needs attention?" },
  { page: "Exceptions", question: "What specifically didn't reconcile, and why?" },
  { page: "GST & ITC", question: "How much tax can the merchant actually reclaim?" },
  { page: "Cash forecast", question: "What lands in the bank next, and how sure are we?" },
  { page: "Journal", question: "What are the accounting entries this implies?" },
  { page: "Audit trail", question: "Show me every decision the engine made." },
  { page: "Marketplace", question: "What changes when paying third-party sellers?" },
  { page: "Verification", question: "Why should I believe any of the above?" },
];

export default function FirstRunGuide() {
  const [open, setOpen] = useState(false);

  return (
    <div className="border-b border-rule">
      <button
        className="flex w-full items-center justify-between py-3 text-left"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
      >
        <span className="text-[13px]">
          <span className="font-medium">New here?</span>{" "}
          <span className="text-ink-soft">
            What this is, what the jargon means, and which page answers which question.
          </span>
        </span>
        <span className="ml-4 text-[12px] text-indigo">{open ? "hide" : "read this first →"}</span>
      </button>

      {open && (
        <div className="pb-7 pt-1">
          <div className="grid grid-cols-1 gap-8 md:grid-cols-[1.15fr_1fr]">
            <div>
              <h3 className="mb-2 font-serif text-[18px] font-semibold">
                The problem, in one paragraph
              </h3>
              <p className="mb-3 max-w-[62ch] text-[13.5px] leading-[1.6] text-ink-soft">
                A merchant sells 68 things online. Razorpay collects the money, keeps a
                fee, adds tax on that fee, subtracts any refunds and disputes, then wires
                one lump sum to the bank a couple of days later. The bank statement shows
                a single credit with no breakdown. The merchant now has three records that
                should agree — their own orders, Razorpay's settlement report, and the
                bank statement — and no easy way to prove they do.
              </p>
              <p className="mb-3 max-w-[62ch] text-[13.5px] leading-[1.6] text-ink-soft">
                Today a person does this by hand, in a spreadsheet, monthly. It is slow,
                and when a number is wrong nobody can say <em>why</em> it is wrong — only
                that it is. That "why" is the part that decides whether anyone needs to
                act.
              </p>
              <p className="max-w-[62ch] text-[13.5px] leading-[1.6] text-ink-soft">
                This engine closes that loop across five stages, reports its own accuracy
                with the denominators stated, and separates variances that need a human
                from ones that are simply how settlements work.
              </p>

              <h3 className="mb-2 mt-6 font-serif text-[18px] font-semibold">
                The one thing worth knowing about the design
              </h3>
              <p className="max-w-[62ch] text-[13.5px] leading-[1.6] text-ink-soft">
                No language model decides where money went. The matching is deterministic
                rules with explicit tolerances, because a reconciliation decision has to
                trace to a specific row and a specific rule — something you can take to an
                auditor. AI is used for explanation on top of decisions already made, and
                for attacking the engine to find its blind spots. That division is
                deliberate and is the whole argument of the{" "}
                <strong className="font-medium text-ink">Verification</strong> page.
              </p>
            </div>

            <div>
              <h3 className="mb-2 font-serif text-[18px] font-semibold">Jargon, plainly</h3>
              <dl className="mb-6">
                {GLOSSARY.map((g) => (
                  <div key={g.term} className="border-b border-dotted border-rule py-2 last:border-b-0">
                    <dt className="text-[13px] font-medium">{g.term}</dt>
                    <dd className="mt-0.5 text-[12.5px] leading-[1.5] text-ink-soft">{g.plain}</dd>
                  </div>
                ))}
              </dl>

              <h3 className="mb-2 font-serif text-[18px] font-semibold">
                Which page answers what
              </h3>
              <ol className="space-y-1.5">
                {READING_ORDER.map((r, i) => (
                  <li key={r.page} className="text-[12.5px] leading-[1.5]">
                    <span className="text-ink-soft">{i + 1}.</span>{" "}
                    <span className="font-medium">{r.page}</span>
                    <span className="text-ink-soft"> — {r.question}</span>
                  </li>
                ))}
              </ol>

              <p className="mt-4 border-l-2 border-indigo pl-3.5 text-[12.5px] leading-[1.55] text-ink-soft">
                Doubt any number on any page? Open the calculator from the top bar and
                check it yourself — it comes preloaded with the exact rates this project
                reconciles against.
              </p>

              <div className="mt-5 border-l-2 border-amber pl-3.5">
                <h3 className="mb-1.5 font-serif text-[15px] font-semibold">
                  Demo credentials, for reviewers
                </h3>
                <p className="mb-2 text-[12.5px] leading-[1.5] text-ink-soft">
                  Overview through Verification need no sign-in. Two pages send data
                  further or write to the ledger, and are credentialed on purpose — the
                  key is checked server-side, so these only work if the backend was
                  started with the matching environment variable set.
                </p>
                <table className="w-full text-[12.5px]">
                  <tbody>
                    <tr className="border-b border-dotted border-rule">
                      <td className="py-1.5 pr-3 font-medium">/ask</td>
                      <td className="py-1.5 pr-3 text-ink-soft">operator role</td>
                      <td className="py-1.5"><code className="bg-paper-sunk px-1.5 py-0.5">ashmit</code></td>
                    </tr>
                    <tr>
                      <td className="py-1.5 pr-3 font-medium">/admin</td>
                      <td className="py-1.5 pr-3 text-ink-soft">admin role</td>
                      <td className="py-1.5"><code className="bg-paper-sunk px-1.5 py-0.5">ashmit123</code></td>
                    </tr>
                  </tbody>
                </table>
                <p className="mt-2 text-[11.5px] leading-[1.5] text-ink-soft">
                  These are demo values only, set via SADHAKA_OPERATOR_KEY and
                  SADHAKA_ADMIN_KEY when the backend was started. In a real deployment
                  each would be a private, unpublished secret — publishing them here is
                  only reasonable because this is a reviewer-facing demo of synthetic data.
                </p>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
