import { useMemo, useState } from "react";
import { useData } from "@/App";
import { PageHead, Section, Empty, Badge } from "@/components/ui";
import { traceEntity, API_BASE } from "@/lib/api";
import { rupees, titleCase } from "@/lib/format";

export default function AuditTrail() {
  const { data } = useData();
  const decisions = data.audit.decisions;

  const [q, setQ] = useState("");
  const [outcome, setOutcome] = useState<"all" | "MATCHED" | "EXCEPTION" | "UNMATCHED">("all");
  const [stage, setStage] = useState<string>("all");
  const [open, setOpen] = useState<number | null>(null);

  const [traceId, setTraceId] = useState("");
  const [trace, setTrace] = useState<string | null>(null);
  const [tracing, setTracing] = useState(false);

  const stages = useMemo(
    () => [...new Set(decisions.map((d) => d.stage))].sort(),
    [decisions]
  );

  const rows = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return decisions.filter((d) => {
      if (outcome !== "all" && d.outcome !== outcome) return false;
      if (stage !== "all" && d.stage !== stage) return false;
      if (!needle) return true;
      return (
        d.subject_id.toLowerCase().includes(needle) ||
        (d.counterpart_id ?? "").toLowerCase().includes(needle) ||
        d.reason.toLowerCase().includes(needle) ||
        d.rule_fired.toLowerCase().includes(needle)
      );
    });
  }, [decisions, q, outcome, stage]);

  async function runTrace() {
    const id = traceId.trim();
    if (!id) { setTrace("Enter an identifier first."); return; }
    setTracing(true);
    const r = await traceEntity(id);
    setTracing(false);
    if ("unavailable" in r) {
      const local = decisions.filter(
        (d) => d.subject_id === id || d.counterpart_id === id
      );
      setTrace(
        local.length
          ? local
              .map(
                (d) =>
                  `[${d.stage}] The engine ${
                    d.outcome === "MATCHED" ? "matched" : d.outcome === "EXCEPTION" ? "raised an exception" : "found no counterpart"
                  } at ${Math.round(d.confidence * 100)}% confidence via rule '${d.rule_fired}'${
                    d.variance_code ? ` (${d.variance_code})` : ""
                  }. ${d.reason}`
              )
              .join("\n\n")
          : `Nothing recorded for '${id}' in the bundled decision log. Connect a live API to search the full trail.`
      );
      return;
    }
    setTrace(r.found ? r.narrative : `No decision was recorded for '${id}'.`);
  }

  return (
    <>
      <PageHead
        title="Audit trail"
        lede="Every decision the engine made, with the rule that produced it and the evidence it held at the time."
        aside={
          <>
            {data.audit.total.toLocaleString()} decisions recorded
            <br />
            showing {decisions.length} · run{" "}
            <code className="rounded-[2px] bg-paper-sunk px-1.5 py-px">{data.audit.run_id}</code>
          </>
        }
      />

      <Section
        title="Ask the trail"
        note="Enter an order, payment, settlement or bank reference. The answer is assembled from the decisions the engine actually recorded — it is the engine's reasoning, not a plausible story written afterwards."
      >
        <div className="mb-4 flex flex-wrap gap-2">
          <input
            className="field min-w-[260px]"
            placeholder="order_2043, pay_…, setl_…, bnk_…"
            value={traceId}
            onChange={(e) => setTraceId(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") runTrace(); }}
          />
          <button className="chip" onClick={runTrace} disabled={tracing}>
            {tracing ? "Reading…" : "Trace this entity"}
          </button>
          {!API_BASE && <Badge tone="amber">searching the bundled log</Badge>}
        </div>
        {trace ? (
          <div
            className="max-w-[80ch] whitespace-pre-wrap border-l-2 border-indigo text-[14px] leading-[1.65]"
            style={{ paddingLeft: 18 }}
          >
            {trace}
          </div>
        ) : (
          <Empty>No entity traced yet.</Empty>
        )}
      </Section>

      <Section title="The decision log" note="Matches and exceptions together, in the order the engine made them.">
        <div className="mb-5 flex flex-wrap items-center gap-2">
          <input
            className="field min-w-[240px]"
            placeholder="Search reasons, ids, rules…"
            value={q}
            onChange={(e) => setQ(e.target.value)}
          />
          {(["all", "MATCHED", "EXCEPTION", "UNMATCHED"] as const).map((o) => (
            <button key={o} className="chip" aria-pressed={outcome === o} onClick={() => setOutcome(o)}>
              {o === "all" ? "All outcomes" : titleCase(o)}
            </button>
          ))}
          <select className="field" value={stage} onChange={(e) => setStage(e.target.value)}>
            <option value="all">All stages</option>
            {stages.map((s) => (
              <option key={s} value={s}>{s}</option>
            ))}
          </select>
          <span className="text-[12.5px] text-ink-soft">{rows.length} shown</span>
        </div>

        {rows.length === 0 ? (
          <Empty>Nothing matches those filters.</Empty>
        ) : (
          <table className="ledger-table">
            <thead>
              <tr>
                <th style={{ width: 92 }}>Outcome</th>
                <th style={{ textAlign: "left" }}>Decision</th>
                <th style={{ width: 78 }}>Conf.</th>
                <th style={{ width: 124 }}>Amount</th>
              </tr>
            </thead>
            <tbody>
              {rows.slice(0, 250).map((d) => {
                const isOpen = open === d.decision_id;
                const tone =
                  d.outcome === "MATCHED" ? "text-credit"
                  : d.outcome === "EXCEPTION" ? "text-debit"
                  : "text-amber";
                return (
                  <>
                    <tr
                      key={d.decision_id}
                      className={`selectable ${isOpen ? "bg-paper-sunk" : ""}`}
                      onClick={() => setOpen(isOpen ? null : d.decision_id)}
                    >
                      <td>
                        <span className={`text-[11.5px] font-semibold ${tone}`}>{d.outcome}</span>
                        <div className="mt-0.5 text-[11px] text-ink-soft">{d.stage.replace("stage", "s")}</div>
                      </td>
                      <td style={{ textAlign: "left", fontFamily: "Inter, sans-serif", fontSize: 13 }}>
                        <div className="max-w-[64ch] leading-[1.5] text-ink-soft">{d.reason}</div>
                        <div className="mt-1 text-[12px] text-ink-soft">
                          {d.subject_type} {d.subject_id}
                          {d.counterpart_id && <> → {d.counterpart_type} {d.counterpart_id}</>}
                        </div>
                      </td>
                      <td>{Math.round((d.confidence ?? 0) * 100)}%</td>
                      <td>{d.amount_subject != null ? rupees(d.amount_subject) : "—"}</td>
                    </tr>
                    {isOpen && (
                      <tr key={`${d.decision_id}-d`}>
                        <td colSpan={4} style={{ background: "var(--paper-sunk)", paddingBottom: 18 }}>
                          <div className="max-w-[80ch] pt-1 font-sans text-[13px] leading-[1.6]">
                            <div className="mt-1">
                              <span className="text-[11.5px] font-semibold text-ink-soft">Rule </span>
                              <code className="bg-paper px-1.5 py-px">{d.rule_fired}</code>
                              {d.variance_code && <> · <Badge>{titleCase(d.variance_code)}</Badge></>}
                            </div>
                            {d.variance_paise != null && d.variance_paise !== 0 && (
                              <div className="mt-2">
                                <span className="text-[11.5px] font-semibold text-ink-soft">Variance </span>
                                <span className="figure">{rupees(d.variance_paise)}</span>
                              </div>
                            )}
                            <pre className="mt-2.5 whitespace-pre-wrap border border-rule bg-paper px-2.5 py-2 text-[12.5px] leading-[1.5]">
{JSON.stringify(d.evidence ?? {}, null, 2)}
                            </pre>
                          </div>
                        </td>
                      </tr>
                    )}
                  </>
                );
              })}
            </tbody>
          </table>
        )}
        {rows.length > 250 && (
          <p className="mt-3 text-[12.5px] text-ink-soft">
            Showing the first 250 of {rows.length}. Narrow the search to see the rest.
          </p>
        )}
      </Section>
    </>
  );
}
