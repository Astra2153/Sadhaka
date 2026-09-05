import { useEffect, useState } from "react";
import {
  LineChart, Line as RLine, ScatterChart, Scatter, XAxis, YAxis, CartesianGrid,
  Tooltip, ResponsiveContainer, Legend, ReferenceLine, ZAxis,
} from "recharts";
import { loadVerification } from "@/lib/api";
import type { VerificationReport } from "@/types";
import {
  PageHead, Section, Figure, FigureRow, Finding, IntervalBar, Loading, Empty,
} from "@/components/ui";

const SERIES_COLOURS = ["var(--indigo)", "var(--debit)", "var(--credit)", "var(--amber)", "#6B4E9B"];

const VERDICT = {
  floor_established: ["text-credit", "floor established"],
  reliable: ["text-credit", "reliable"],
  blind_spot: ["text-debit", "BLIND SPOT"],
  unreliable: ["text-debit", "UNRELIABLE"],
  underpowered: ["text-amber", "underpowered"],
} as const;

export default function Verification() {
  const [v, setV] = useState<VerificationReport | null>(null);

  useEffect(() => {
    let cancelled = false;
    loadVerification().then((r) => { if (!cancelled) setV(r.data); });
    return () => { cancelled = true; };
  }, []);

  if (!v) return <Loading what="the verification report" />;

  const blind = v.detection_limits.filter((d) => d.verdict === "blind_spot" || d.verdict === "unreliable");
  const floors = v.detection_limits.filter((d) => d.verdict === "floor_established" && d.lod95);
  const moneyFloors = floors.filter((d) => d.unit === "paise");
  const tightest = moneyFloors.length
    ? moneyFloors.reduce((a, b) => ((a.lod95 ?? 1e18) < (b.lod95 ?? 1e18) ? a : b))
    : null;

  /* Detection curves: one series per money-denominated fault, on a shared
     log magnitude axis. Recharts needs the series flattened onto shared x
     keys, so the ladder is rebuilt as a row per magnitude. */
  const curveSeries = v.detection_limits.filter((d) => d.unit === "paise" && d.levels.length > 1);
  const magnitudes = [...new Set(curveSeries.flatMap((s) => s.levels.map((l) => l.magnitude)))].sort((a, b) => a - b);
  const curveData = magnitudes.map((mag) => {
    const row: Record<string, number | string> = {
      mag,
      label: mag >= 100 ? `₹${Math.round(mag / 100)}` : `₹${(mag / 100).toFixed(2)}`,
    };
    for (const s of curveSeries) {
      const lv = s.levels.find((l) => l.magnitude === mag);
      if (lv) row[s.fault_type] = Number((lv.detection_rate * 100).toFixed(1));
    }
    return row;
  });

  const calPoints = v.calibration.bins.map((b) => ({
    claimed: Number((b.claimed_confidence * 100).toFixed(1)),
    observed: Number((b.observed_accuracy * 100).toFixed(1)),
    n: b.count,
    direction: b.direction,
  }));

  const actionableCfs = v.counterfactuals.filter((c) => c.counterfactual.actionable);

  return (
    <>
      <PageHead
        title="Verification"
        lede="Sadhaka attacking itself, and reporting where it fails."
        aside={
          <>
            profile <strong>{v.profile}</strong>
            <br />
            {v.total_attack_trials.toLocaleString()} injected faults ·{" "}
            {v.calibration_samples.toLocaleString()} decisions scored
            <br />
            completed in {v.elapsed_seconds}s
          </>
        }
      />

      <div className="ruled py-7">
        <p className="mb-3.5 max-w-[70ch] font-serif text-[19px] leading-[1.6]">
          Track 04's premise is that verification capacity, not generation speed, is the
          bottleneck. This page applies that premise to the reconciliation engine itself.
        </p>
        <p className="max-w-[76ch] text-[15px] text-ink-soft">
          Eleven hand-planted faults prove an engine catches eleven hand-planted faults.
          That is a demonstration, not evidence. So the engine is attacked programmatically
          instead — thousands of faults injected one at a time, across nine kinds and a
          range of magnitudes — to answer three questions its own report cannot: what is
          the smallest fault it can actually catch, does its confidence score mean
          anything, and where does it break.
        </p>
      </div>

      <FigureRow>
        <Figure value={v.total_attack_trials.toLocaleString()} caption={`faults injected, one at a time, across ${v.detection_limits.length} kinds`} />
        <Figure
          value={tightest?.lod95_display ?? "—"}
          caption="smallest money fault reliably detected"
          tone={tightest ? "credit" : "ink"}
        />
        <Figure
          value={blind.length}
          caption={`blind spot${blind.length === 1 ? "" : "s"} found and reported`}
          tone={blind.length ? "debit" : "credit"}
        />
        <Figure value={v.calibration.ece.toFixed(3)} caption="expected calibration error on the confidence score" />
      </FigureRow>

      <Section
        title="Limit of detection"
        note="Borrowed from analytical chemistry, where no instrument claims to measure everything — it states the smallest quantity it can distinguish from noise and refuses to claim anything below it. Faults smaller than the engine's own declared tolerance are excluded, because not detecting those is documented, correct behaviour rather than a miss."
      >
        <table className="ledger-table">
          <thead>
            <tr>
              <th style={{ textAlign: "left" }}>Fault injected</th>
              <th>Trials</th>
              <th>Detected</th>
              <th style={{ textAlign: "left", paddingLeft: 22 }}>95% interval</th>
              <th>Floor</th>
              <th style={{ textAlign: "left", paddingLeft: 20 }}>Verdict</th>
            </tr>
          </thead>
          <tbody>
            {v.detection_limits.map((d) => {
              const [cls, label] = VERDICT[d.verdict] ?? ["text-ink-soft", d.verdict];
              return (
                <tr key={d.fault_type}>
                  <td style={{ textAlign: "left" }}>
                    <span className="font-medium">{d.label}</span>
                    <div className="mt-0.5 text-[11.5px] font-normal text-ink-soft">{d.fault_type}</div>
                  </td>
                  <td>{d.aggregate_trials}</td>
                  <td>{Math.round(d.aggregate_rate * 100)}%</td>
                  <td style={{ textAlign: "left", paddingLeft: 22 }}>
                    <IntervalBar low={d.aggregate_ci[0]} high={d.aggregate_ci[1]} point={d.aggregate_rate} />
                  </td>
                  <td>{d.lod95_display ?? "—"}</td>
                  <td style={{ textAlign: "left", paddingLeft: 20, fontFamily: "Inter, sans-serif" }}>
                    <span className={`text-[11.5px] font-semibold ${cls}`}>{label}</span>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>

        <p className="mt-4 max-w-[76ch] text-[12.5px] leading-[1.55] text-ink-soft">
          Rates are aggregated across every magnitude above tolerance, which is far better
          powered than any single level. The interval is a Wilson score interval. A point
          estimate from a handful of trials is not evidence, so the harness refuses to
          claim a floor it cannot support — and says so rather than reporting a number
          that would look better.
        </p>

        <div className="mt-7 h-[320px] w-full">
          <ResponsiveContainer>
            <LineChart data={curveData} margin={{ top: 12, right: 20, bottom: 22, left: 0 }}>
              <CartesianGrid vertical={false} />
              <XAxis
                dataKey="label"
                tickLine={false}
                axisLine={{ stroke: "var(--ink)" }}
                label={{ value: "fault magnitude", position: "insideBottom", offset: -12, fontSize: 11, fill: "var(--ink-soft)" }}
              />
              <YAxis domain={[0, 100]} tickFormatter={(v2) => `${v2}%`} tickLine={false} axisLine={false} width={44} />
              <Tooltip formatter={(val: number, name) => [`${val}% detected`, String(name).replace(/_/g, " ").toLowerCase()]} />
              <ReferenceLine y={95} stroke="var(--credit)" strokeDasharray="4 3" label={{ value: "95%", position: "right", fontSize: 10, fill: "var(--credit)" }} />
              <Legend
                wrapperStyle={{ fontSize: 11.5, color: "var(--ink-soft)" }}
                formatter={(val) => String(val).replace(/_/g, " ").toLowerCase()}
              />
              {curveSeries.map((s, i) => (
                <RLine
                  key={s.fault_type}
                  type="monotone"
                  dataKey={s.fault_type}
                  stroke={SERIES_COLOURS[i % SERIES_COLOURS.length]}
                  strokeWidth={1.8}
                  dot={{ r: 2.6 }}
                  connectNulls
                />
              ))}
            </LineChart>
          </ResponsiveContainer>
        </div>
      </Section>

      <Section
        title="Is the confidence score honest?"
        note="The engine attaches a confidence to every match. That number is a claim about the world: of the matches scored at 0.85, roughly 85% should be correct. Almost nothing checks. A tool that says 95% and is right 60% of the time is worse than one that says nothing, because the number invites trust it has not earned."
      >
        <div className="grid grid-cols-1 gap-10 md:grid-cols-2">
          <div>
            <div className="h-[330px] w-full">
              <ResponsiveContainer>
                <ScatterChart margin={{ top: 16, right: 20, bottom: 30, left: 4 }}>
                  <CartesianGrid />
                  <XAxis
                    type="number" dataKey="claimed" domain={[60, 100]} unit="%"
                    tickLine={false} axisLine={{ stroke: "var(--ink)" }}
                    label={{ value: "confidence the engine claimed", position: "insideBottom", offset: -18, fontSize: 11, fill: "var(--ink-soft)" }}
                  />
                  <YAxis
                    type="number" dataKey="observed" domain={[60, 100]} unit="%"
                    tickLine={false} axisLine={false} width={48}
                    label={{ value: "how often it was right", angle: -90, position: "insideLeft", fontSize: 11, fill: "var(--ink-soft)" }}
                  />
                  <ZAxis type="number" dataKey="n" range={[70, 420]} />
                  <ReferenceLine
                    segment={[{ x: 60, y: 60 }, { x: 100, y: 100 }]}
                    stroke="var(--ink-soft)" strokeDasharray="4 4"
                  />
                  <Tooltip
                    cursor={{ strokeDasharray: "3 3" }}
                    formatter={(val: number, name) => [`${val}%`, name === "claimed" ? "claimed" : "observed"]}
                  />
                  <Scatter data={calPoints} fill="var(--indigo)" fillOpacity={0.78} />
                </ScatterChart>
              </ResponsiveContainer>
            </div>
            <p className="mt-2 max-w-[52ch] text-[12.5px] text-ink-soft">
              The dashed diagonal is perfect calibration. Points above it mean the engine
              is right more often than it claims; below means overconfident. Circle area
              is the number of decisions in that band.
            </p>
          </div>

          <div>
            <h3 className="mb-3 text-[16px] font-semibold">Reliability by confidence band</h3>
            <table className="ledger-table">
              <thead>
                <tr>
                  <th style={{ textAlign: "left" }}>Claimed</th>
                  <th>Observed</th><th>Gap</th><th>N</th>
                  <th style={{ textAlign: "left", paddingLeft: 18 }}>Direction</th>
                </tr>
              </thead>
              <tbody>
                {v.calibration.bins.map((b) => (
                  <tr key={b.range}>
                    <td style={{ textAlign: "left" }}>{(b.claimed_confidence * 100).toFixed(1)}%</td>
                    <td>{(b.observed_accuracy * 100).toFixed(1)}%</td>
                    <td className={b.gap < 0 ? "text-debit" : "text-ink-soft"}>
                      {b.gap >= 0 ? "+" : ""}{(b.gap * 100).toFixed(1)}%
                    </td>
                    <td>{b.count}</td>
                    <td style={{ textAlign: "left", paddingLeft: 18, fontFamily: "Inter, sans-serif", fontSize: 12.5 }}>
                      {b.direction}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>

            <div className="mt-6 space-y-1.5 text-[13.5px]">
              <div>Expected calibration error <strong className="figure">{v.calibration.ece}</strong></div>
              <div>
                Brier score <strong className="figure">{v.calibration.brier_score}</strong>{" "}
                <span className="text-[12px] text-ink-soft">(lower is better)</span>
              </div>
              <div>Overall accuracy <strong className="figure">{(v.calibration.overall_accuracy * 100).toFixed(1)}%</strong></div>
              <div>Mean stated confidence <strong className="figure">{(v.calibration.mean_confidence * 100).toFixed(1)}%</strong></div>
            </div>
          </div>
        </div>

        <Finding
          title={
            v.calibration.bins.some((b) => b.direction === "overconfident")
              ? "Overconfident in at least one band"
              : "Underconfident, which is the safe direction"
          }
          tone={v.calibration.bins.some((b) => b.direction === "overconfident") ? "bad" : "good"}
        >
          {v.calibration.verdict}
        </Finding>
      </Section>

      <Section title="Where it breaks" note="Reported rather than tuned away. A verification report that only ever confirms the tool works is marketing, not verification.">
        {blind.length === 0 && (v.underpowered ?? []).length === 0 ? (
          <Finding title="No blind spots at the magnitudes tested" tone="good">
            Every fault type reached a reliable detection floor. That is a statement
            about these fault types at these magnitudes, not a claim that nothing can
            get past the engine.
          </Finding>
        ) : (
          <>
            {v.blind_spots.map((b) => (
              <Finding key={b.fault_type} title={b.label} tone="bad">{b.statement}</Finding>
            ))}
            {(v.underpowered ?? []).map((u) => (
              <Finding key={u.fault_type} title={`${u.label} — measurement underpowered`} tone="warn">
                {u.statement}
              </Finding>
            ))}
          </>
        )}

        <Finding title="Baseline on clean data" tone="note">
          {v.baseline.note} On untampered data the engine raises{" "}
          {v.baseline.exceptions_on_clean_data} exceptions across{" "}
          {v.baseline.entities_flagged} entities.
        </Finding>
      </Section>

      <Section
        title="Counterfactual explanations"
        note="An exception that says 'this did not match' tells a finance person a problem exists. It does not tell them what to do. The size and shape of the required change is diagnostic — a gap equal to the fee means the fee was deducted twice; a gap equal to 18% of the fee means the GST leg is duplicated or missing."
      >
        {actionableCfs.length === 0 ? (
          <Empty>No exception in this run admits a single-change fix.</Empty>
        ) : (
          actionableCfs.map((c) => (
            <div key={c.subject_id} className="border-b border-rule py-4 last:border-b-0">
              <div className="flex flex-wrap items-baseline gap-3.5">
                <span className="text-[11.5px] font-semibold text-debit">{c.variance_code}</span>
                <span className="text-[12.5px] text-ink-soft">{c.subject_id}</span>
              </div>
              <p className="mt-1.5 max-w-[74ch] text-[13px] leading-[1.5] text-ink-soft">
                {c.original_reason}
              </p>
              <div className="mt-2.5 max-w-[74ch] border-l-2 border-indigo pl-4.5 text-[14px] leading-[1.6]" style={{ paddingLeft: 18 }}>
                {c.counterfactual.narrative}
              </div>
              {c.counterfactual.changes && c.counterfactual.changes.length > 0 && (
                <div className="mt-2.5 space-y-1 pl-[18px] text-[12.5px] text-ink-soft">
                  {c.counterfactual.changes.map((ch, i) => (
                    <div key={i}>
                      {ch.field}: <code className="bg-paper-sunk px-1.5 py-px">{ch.current}</code>
                      {" → "}
                      <code className="bg-paper-sunk px-1.5 py-px">{ch.required}</code>
                      {ch.delta && <> ({ch.delta})</>}
                    </div>
                  ))}
                </div>
              )}
            </div>
          ))
        )}
      </Section>

      <Section title="Reproducing this">
        <pre className="max-w-[70ch] border border-rule bg-paper-sunk px-4 py-3.5 font-sans text-[12.5px] leading-[1.8]">
{`python3 src/run_verification.py --quick      # ~1 min, coarse
python3 src/run_verification.py              # standard
python3 src/run_verification.py --thorough   # establishes detection floors`}
        </pre>
        <p className="mt-3 max-w-[80ch] text-[12.5px] text-ink-soft">
          The harness runs the real matching engine with its audit writes discarded, so
          what is measured is the same code that reconciles the money — not a
          reimplementation that could drift from it.
        </p>
      </Section>
    </>
  );
}
