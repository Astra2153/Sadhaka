import { useMemo, useState } from "react";
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Cell,
} from "recharts";
import { useData } from "@/App";
import { PageHead, Section, Empty, Badge } from "@/components/ui";
import { rupees, rupeesShort, isBenign, titleCase } from "@/lib/format";
import type { Decision } from "@/types";

type Filter = "all" | "actionable" | "benign" | string;

const money = (d: Decision) =>
  Math.abs(d.variance_paise ?? d.amount_subject ?? 0);

export default function Exceptions() {
  const { data } = useData();
  const all = data.exceptions.exceptions;
  const [filter, setFilter] = useState<Filter>("all");
  const [open, setOpen] = useState<number | null>(null);

  const byCode = useMemo(() => {
    const m = new Map<string, { code: string; value: number; count: number; benign: boolean }>();
    for (const e of all) {
      const code = e.variance_code ?? "UNEXPLAINED";
      const cur = m.get(code) ?? { code, value: 0, count: 0, benign: isBenign(code) };
      cur.value += money(e);
      cur.count += 1;
      m.set(code, cur);
    }
    return [...m.values()].sort((a, b) => b.value - a.value);
  }, [all]);

  const rows = useMemo(() => {
    let r = all;
    if (filter === "actionable") r = r.filter((e) => !isBenign(e.variance_code));
    else if (filter === "benign") r = r.filter((e) => isBenign(e.variance_code));
    else if (filter !== "all") r = r.filter((e) => (e.variance_code ?? "UNEXPLAINED") === filter);
    return [...r].sort((a, b) => money(b) - money(a));
  }, [all, filter]);

  const chips: [Filter, string][] = [
    ["all", `All (${data.exceptions.count})`],
    ["actionable", `Needs action (${data.exceptions.actionable})`],
    ["benign", `Explained (${data.exceptions.benign})`],
    ...byCode.map((c) => [c.code, `${titleCase(c.code)} (${c.count})`] as [Filter, string]),
  ];

  return (
    <>
      <PageHead
        title="Exceptions"
        lede="Every rupee the engine could not place, with the reason it recorded at the time."
        aside={
          <>
            {data.exceptions.actionable} actionable · {data.exceptions.benign} explained
            <br />
            run <code className="rounded-[2px] bg-paper-sunk px-1.5 py-px">{data.exceptions.run_id}</code>
          </>
        }
      />

      <Section
        title="Value at stake by cause"
        note="Benign codes are drawn hollow — they carry rupees but need no action. A bank holiday delay is not missing money, and drawing it the same way as an unexplained shortfall would waste the reviewer's attention on the wrong row."
      >
        <div className="h-[240px] w-full max-w-[760px]">
          <ResponsiveContainer>
            <BarChart
              data={byCode.map((c) => ({ ...c, name: titleCase(c.code) }))}
              layout="vertical"
              margin={{ top: 4, right: 60, bottom: 4, left: 108 }}
            >
              <CartesianGrid horizontal={false} />
              <XAxis type="number" tickFormatter={(v) => rupeesShort(v)} tickLine={false} axisLine={false} />
              <YAxis type="category" dataKey="name" width={140} tickLine={false} axisLine={{ stroke: "var(--ink)" }} />
              <Tooltip
                cursor={{ fill: "var(--paper-sunk)" }}
                formatter={(v: number, _n, p) => [
                  `${rupees(v)} across ${p.payload.count} exception${p.payload.count === 1 ? "" : "s"}`,
                  p.payload.benign ? "explained" : "needs action",
                ]}
              />
              <Bar dataKey="value">
                {byCode.map((c, i) => (
                  <Cell
                    key={i}
                    fill={c.benign ? "transparent" : "var(--debit)"}
                    stroke={c.benign ? "var(--ink-soft)" : "none"}
                    strokeWidth={c.benign ? 1 : 0}
                    opacity={c.benign ? 1 : 0.82}
                  />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>
      </Section>

      <Section title="The ledger" note="Sorted by money at stake. Select a row to see the rule that fired and the evidence recorded at decision time.">
        <div className="mb-5 flex flex-wrap gap-2">
          {chips.map(([key, label]) => (
            <button
              key={String(key)}
              className="chip"
              aria-pressed={filter === key}
              onClick={() => { setFilter(key); setOpen(null); }}
            >
              {label}
            </button>
          ))}
        </div>

        {rows.length === 0 ? (
          <Empty>Nothing in this category.</Empty>
        ) : (
          <table className="ledger-table">
            <thead>
              <tr>
                <th style={{ width: 168 }}>Code</th>
                <th style={{ textAlign: "left" }}>What the engine found</th>
                <th style={{ width: 84 }}>Confidence</th>
                <th style={{ width: 132 }}>Value</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((e) => {
                const code = e.variance_code ?? "UNEXPLAINED";
                const benign = isBenign(code);
                const isOpen = open === e.decision_id;
                return (
                  <>
                    <tr
                      key={e.decision_id}
                      className={`selectable ${isOpen ? "bg-paper-sunk" : ""}`}
                      onClick={() => setOpen(isOpen ? null : e.decision_id)}
                    >
                      <td>
                        <span className={`whitespace-nowrap text-[12.5px] font-semibold ${benign ? "text-ink-soft" : "text-debit"}`}>
                          {titleCase(code)}
                        </span>
                        <div className="mt-0.5 text-[11.5px] text-ink-soft">
                          {benign ? "explained" : "needs action"}
                        </div>
                      </td>
                      <td style={{ textAlign: "left", fontFamily: "Inter, sans-serif", fontSize: 13 }}>
                        <div className="max-w-[62ch] leading-[1.5] text-ink-soft">{e.reason}</div>
                        <div className="mt-1 text-[12px] text-ink-soft">
                          {e.subject_type} {e.subject_id}
                        </div>
                      </td>
                      <td>{Math.round((e.confidence ?? 0) * 100)}%</td>
                      <td>{e.variance_paise ? rupees(e.variance_paise) : (e.amount_subject_display ?? "—")}</td>
                    </tr>
                    {isOpen && (
                      <tr key={`${e.decision_id}-d`}>
                        <td colSpan={4} style={{ background: "var(--paper-sunk)", paddingBottom: 18 }}>
                          <div className="max-w-[80ch] pt-1 font-sans text-[13px] leading-[1.6]">
                            <dl>
                              <dt className="mt-2 text-[11.5px] font-semibold text-ink-soft">What this code means</dt>
                              <dd className="mt-0.5">{e.code_meaning}</dd>
                              <dt className="mt-2.5 text-[11.5px] font-semibold text-ink-soft">Rule that fired</dt>
                              <dd className="mt-0.5">
                                <code className="bg-paper px-1.5 py-px">{e.rule_fired}</code>{" "}
                                <span className="text-ink-soft">stage {e.stage}</span>
                              </dd>
                              {e.counterpart_id && (
                                <>
                                  <dt className="mt-2.5 text-[11.5px] font-semibold text-ink-soft">Compared against</dt>
                                  <dd className="mt-0.5">{e.counterpart_type} {e.counterpart_id}</dd>
                                </>
                              )}
                              <dt className="mt-2.5 text-[11.5px] font-semibold text-ink-soft">
                                Evidence recorded at decision time
                              </dt>
                              <dd>
                                <pre className="mt-1.5 whitespace-pre-wrap border border-rule bg-paper px-2.5 py-2 text-[12.5px] leading-[1.5]">
{JSON.stringify(e.evidence ?? {}, null, 2)}
                                </pre>
                              </dd>
                            </dl>
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
      </Section>
    </>
  );
}
