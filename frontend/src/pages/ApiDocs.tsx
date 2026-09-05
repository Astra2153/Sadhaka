import { useState } from "react";
import { PageHead, Section, Badge, Finding } from "@/components/ui";
import { API_BASE } from "@/lib/api";

/**
 * Human-readable API reference.
 *
 * Swagger at /docs is generated from the schema and is the right tool for
 * trying a call. It is the wrong tool for understanding what to call and in
 * what order, because it lists endpoints alphabetically inside tags with no
 * sense of sequence or purpose. This page fills that gap: what each endpoint
 * is for, when you would reach for it, and a copyable example.
 */

interface Endpoint {
  method: "GET" | "POST";
  path: string;
  what: string;
  when: string;
  example?: string;
}

const GROUPS: { name: string; blurb: string; endpoints: Endpoint[] }[] = [
  {
    name: "Start here",
    blurb:
      "Nothing else returns data until a run exists. If an endpoint gives you a 503, it is almost always because the pipeline has not been executed yet.",
    endpoints: [
      {
        method: "GET",
        path: "/health",
        what: "Liveness check, plus whether any run has been recorded.",
        when: "First call when something is 404-ing and you are not sure if the server or the data is the problem.",
      },
      {
        method: "POST",
        path: "/run",
        what: "Executes the full five-stage reconciliation and returns the summary.",
        when: "To generate a fresh run from the API instead of the command line. Creates a new run rather than overwriting the previous one.",
      },
      {
        method: "GET",
        path: "/runs",
        what: "Every run recorded, newest first, with its decision count.",
        when: "To find a specific run_id you want to query historically.",
      },
    ],
  },
  {
    name: "Reading results",
    blurb:
      "All of these read from the SQLite audit trail. Pass ?run_id= to query a historical run; omit it to get the most recent.",
    endpoints: [
      {
        method: "GET",
        path: "/summary",
        what: "Headline metrics: throughput, four match rates with denominators, exception split, money totals.",
        when: "The single call that answers 'how did this run go'.",
      },
      {
        method: "GET",
        path: "/exceptions",
        what: "Everything the engine could not place, each with the reason it recorded.",
        when: "Triage. Add ?kind=actionable to skip the benign ones, or ?code=FEE_DEDUCTION to isolate one cause.",
        example: "/exceptions?kind=actionable&limit=20",
      },
      {
        method: "GET",
        path: "/matches",
        what: "Successful matches with the rule that produced them and the confidence.",
        when: "To audit what the engine accepted, not just what it rejected.",
      },
      {
        method: "GET",
        path: "/audit",
        what: "The full decision log — matches and exceptions together, filterable and searchable.",
        when: "The source of truth. Everything else on this list is a view over it.",
        example: "/audit?outcome=EXCEPTION&search=refund&limit=50",
      },
      {
        method: "GET",
        path: "/trace/{entity_id}",
        what: "Every decision made about one entity, with a plain-English narrative.",
        when: "Answering 'why didn't order_2007 reconcile' without reading the whole log.",
        example: "/trace/order_2007",
      },
      {
        method: "GET",
        path: "/report/pdf",
        what: "Generates and downloads an audit-ready PDF: executive summary, full exception schedule, GST/ITC statement, journal entries with trial balance, forward cash position, and a verification appendix.",
        when: "Handing the reconciliation to someone who doesn't have this app — an accountant, a GST officer, a file. Regenerated fresh from current output/ files on every call.",
      },
    ],
  },
  {
    name: "Domain views",
    blurb: "Stage-specific outputs that need their own shape.",
    endpoints: [
      {
        method: "GET",
        path: "/gst",
        what: "GST position, invoice tie-out, ITC eligibility with any blockers named, effective rates per instrument.",
        when: "Tax reconciliation, and checking whether input tax credit is actually claimable.",
      },
      {
        method: "GET",
        path: "/forecast",
        what: "Forward cash position with a lag learned from observed history and a confidence band.",
        when: "Answering 'how much lands this week'.",
      },
      {
        method: "GET",
        path: "/journal",
        what: "Double-entry postings plus the trial balance.",
        when: "Handing the reconciliation to an accounting system.",
      },
      {
        method: "GET",
        path: "/marketplace",
        what: "Runs the Route/split-payout scenario where 194-O TDS and Section 52 TCS apply.",
        when: "Demonstrating the statutory logic the default pipeline correctly does not apply.",
      },
    ],
  },
  {
    name: "Verification",
    blurb:
      "Populate these by running `python src/run_verification.py --thorough` first — they read a separate report artifact, not the pipeline's audit trail.",
    endpoints: [
      {
        method: "GET",
        path: "/verification",
        what: "The full adversarial report: detection limits, calibration, blind spots, counterfactuals.",
        when: "Evidence that the engine works, as opposed to the engine's own claim that it does.",
      },
      {
        method: "GET",
        path: "/verification/detection-limits",
        what: "Detection rate by fault magnitude, with Wilson confidence intervals.",
        when: "Answering 'what is the smallest error this catches'.",
      },
      {
        method: "GET",
        path: "/verification/calibration",
        what: "Whether the confidence scores are honest, measured against ground truth.",
        when: "Deciding how much to trust a stated confidence.",
      },
      {
        method: "POST",
        path: "/verification/run",
        what: "Runs the harness on demand. ?profile=quick|standard|thorough.",
        when: "Regenerating the report without the command line. Thorough takes minutes.",
        example: "/verification/run?profile=quick",
      },
    ],
  },
  {
    name: "Question answering",
    blurb:
      "Retrieval-grounded, not free-form chat. The question pulls real audit-trail rows first; the model only phrases what was retrieved.",
    endpoints: [
      {
        method: "POST",
        path: "/qa",
        what: "Ask a natural-language question about a run. Body: { \"question\": \"...\" }",
        when: "Exploratory questions. Returns grounded_rows so you can verify the answer against source data.",
      },
      {
        method: "GET",
        path: "/qa/status",
        what: "Whether an LLM is configured, and which model.",
        when: "Diagnosing why answers look templated rather than phrased.",
      },
    ],
  },
  {
    name: "Reference",
    blurb: "Static. Not run-specific.",
    endpoints: [
      {
        method: "GET",
        path: "/config",
        what: "The rates and tolerances the engine ran with, plus the statutory notes.",
        when: "A match rate is not interpretable without the tolerances that produced it.",
      },
      {
        method: "GET",
        path: "/variance-codes",
        what: "The full variance taxonomy and which codes are benign vs actionable.",
        when: "Understanding what a code on the exceptions page actually means.",
      },
    ],
  },
];

export default function ApiDocs() {
  const [copied, setCopied] = useState<string | null>(null);
  const base = API_BASE || "http://127.0.0.1:8000";

  function copy(text: string) {
    navigator.clipboard?.writeText(text);
    setCopied(text);
    setTimeout(() => setCopied(null), 1400);
  }

  return (
    <>
      <PageHead
        title="API reference"
        lede="Every endpoint, what it is for, and when you would reach for it."
        aside={
          <>
            base URL <code className="rounded-[2px] bg-paper-sunk px-1.5 py-px">{base}</code>
            <br />
            {GROUPS.reduce((n, g) => n + g.endpoints.length, 0)} documented endpoints
          </>
        }
      />

      <Section
        title="Three ways to call this"
        note="Swagger is best for trying a single call interactively. The collections are better for working through several, and for anyone who would rather not leave their editor."
      >
        <div className="grid grid-cols-1 gap-5 md:grid-cols-3">
          <div className="border-l-2 border-indigo pl-4">
            <h3 className="mb-1 text-[15px] font-semibold">Swagger UI</h3>
            <p className="text-[13px] leading-[1.5] text-ink-soft">
              Interactive, auto-generated from the live schema, with try-it-out buttons.
              Open{" "}
              <a
                href={`${base}/docs`}
                target="_blank"
                rel="noreferrer"
                className="border-b border-rule-strong text-indigo hover:border-indigo"
              >
                {base}/docs
              </a>{" "}
              with the backend running.
            </p>
          </div>
          <div className="border-l-2 border-rule-strong pl-4">
            <h3 className="mb-1 text-[15px] font-semibold">Postman</h3>
            <p className="text-[13px] leading-[1.5] text-ink-soft">
              Import{" "}
              <code className="bg-paper-sunk px-1.5 py-px text-[12px]">
                Backend/docs/sadhaka.postman_collection.json
              </code>{" "}
              — all endpoints, grouped, with a baseUrl variable already set.
            </p>
          </div>
          <div className="border-l-2 border-rule-strong pl-4">
            <h3 className="mb-1 text-[15px] font-semibold">VS Code</h3>
            <p className="text-[13px] leading-[1.5] text-ink-soft">
              Open{" "}
              <code className="bg-paper-sunk px-1.5 py-px text-[12px]">
                Backend/docs/requests.http
              </code>{" "}
              with the REST Client extension and click "Send Request" above any
              block. No install beyond the extension.
            </p>
          </div>
        </div>

        <Finding title="Start the backend first" tone="note">
          <code className="bg-paper-sunk px-1.5 py-px">uvicorn api.main:app --reload --port 8000</code>{" "}
          from the <code className="bg-paper-sunk px-1.5 py-px">Backend/</code> folder.
          Then <code className="bg-paper-sunk px-1.5 py-px">POST /run</code> once, or run{" "}
          <code className="bg-paper-sunk px-1.5 py-px">python src/run_pipeline.py</code>, so
          there is a run for the reporting endpoints to read.
        </Finding>
      </Section>

      {GROUPS.map((g) => (
        <Section key={g.name} title={g.name} note={g.blurb}>
          <div className="space-y-4">
            {g.endpoints.map((e) => {
              const url = `${base}${e.example ?? e.path}`;
              return (
                <div key={e.path} className="border-b border-rule pb-4 last:border-b-0">
                  <div className="mb-1.5 flex flex-wrap items-baseline gap-2.5">
                    <Badge tone={e.method === "POST" ? "amber" : "indigo"}>{e.method}</Badge>
                    <code className="font-serif text-[15px] font-medium">{e.path}</code>
                    <button
                      className="ml-auto text-[11.5px] text-ink-soft underline"
                      onClick={() => copy(url)}
                      title="Copy full URL"
                    >
                      {copied === url ? "copied" : "copy URL"}
                    </button>
                  </div>
                  <p className="max-w-[76ch] text-[13.5px] leading-[1.55]">{e.what}</p>
                  <p className="mt-1 max-w-[76ch] text-[12.5px] leading-[1.5] text-ink-soft">
                    <span className="font-medium">When:</span> {e.when}
                  </p>
                  {e.example && (
                    <code className="mt-2 inline-block bg-paper-sunk px-2 py-1 text-[12px]">
                      {e.example}
                    </code>
                  )}
                </div>
              );
            })}
          </div>
        </Section>
      ))}

      <Section title="Response conventions" note="Consistent across every endpoint, so you can rely on them rather than checking each time.">
        <ul className="max-w-[80ch] space-y-2.5 text-[13.5px] leading-[1.55] text-ink-soft">
          <li>
            <strong className="text-ink">Amounts ending in _paise are integers</strong> in the
            smallest currency unit, matching Razorpay's own API convention. Fields without
            the suffix are pre-formatted display strings. Nothing is a float, because binary
            floating point cannot represent 0.01 exactly.
          </li>
          <li>
            <strong className="text-ink">Every figure traces to the audit trail.</strong> No
            endpoint recomputes a number for display. If an endpoint and{" "}
            <code className="bg-paper-sunk px-1.5 py-px text-[12px]">output/audit_trail.db</code>{" "}
            could ever disagree, the audit trail would be decorative.
          </li>
          <li>
            <strong className="text-ink">503 means no data yet</strong>, not a server fault —
            run the pipeline or the verification harness first. 404 means the specific run or
            entity does not exist.
          </li>
          <li>
            <strong className="text-ink">run_id is optional everywhere</strong> it appears.
            Omit it for the latest run; pass it to query history.
          </li>
        </ul>
      </Section>
    </>
  );
}
