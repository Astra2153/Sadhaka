/**
 * Shared primitives.
 *
 * Kept small and unopinionated. Each one exists because it appears on three or
 * more pages, not because a component library says a page should be assembled
 * from boxes.
 */

import type { ReactNode } from "react";

/* ---------- page structure ---------- */

export function Section({
  title, note, children, id,
}: { title?: string; note?: ReactNode; children: ReactNode; id?: string }) {
  return (
    <section id={id} className="ruled animate-settle py-9">
      {title && <h2 className="mb-1 text-[24px] font-semibold">{title}</h2>}
      {note && (
        <p className="mb-6 max-w-[76ch] text-[14px] text-ink-soft">{note}</p>
      )}
      {children}
    </section>
  );
}

export function PageHead({
  title, lede, aside,
}: { title: string; lede?: ReactNode; aside?: ReactNode }) {
  return (
    <header className="flex flex-wrap items-end justify-between gap-8 border-b-2 border-ink pb-4 pt-9">
      <div>
        <h1 className="mb-2 text-[40px] font-semibold leading-none">{title}</h1>
        {lede && (
          <p className="max-w-[58ch] font-serif text-[17px] italic text-ink-soft">
            {lede}
          </p>
        )}
      </div>
      {aside && <div className="text-right text-[12.5px] leading-[1.7] text-ink-soft">{aside}</div>}
    </header>
  );
}

/* ---------- figures ---------- */

export function Figure({
  value, caption, tone = "ink", size = "lg",
}: {
  value: ReactNode;
  caption: ReactNode;
  tone?: "ink" | "credit" | "debit" | "indigo" | "amber";
  size?: "lg" | "md" | "sm";
}) {
  const toneCls = {
    ink: "text-ink", credit: "text-credit", debit: "text-debit",
    indigo: "text-indigo", amber: "text-amber",
  }[tone];
  const sizeCls = { lg: "text-[36px]", md: "text-[28px]", sm: "text-[21px]" }[size];
  return (
    <div>
      <div className={`figure leading-none ${sizeCls} ${toneCls}`}>{value}</div>
      <div className="mt-1.5 max-w-[24ch] text-[12.5px] text-ink-soft">{caption}</div>
    </div>
  );
}

export function FigureRow({ children }: { children: ReactNode }) {
  return (
    <div className="stagger flex flex-wrap gap-x-11 gap-y-6 py-6">{children}</div>
  );
}

/* ---------- meters & bars ---------- */

export function Meter({ pct, tone }: { pct: number; tone?: "warn" | "bad" }) {
  return (
    <div className={`meter ${tone ?? ""}`} role="img" aria-label={`${pct.toFixed(1)} percent`}>
      <i style={{ width: `${Math.max(0, Math.min(100, pct))}%` }} />
    </div>
  );
}

/** A rate with its denominator. A percentage without one is not interpretable,
 *  so the denominator is part of the component rather than optional. */
export function RateRow({
  label, pct, denominator,
}: { label: string; pct: number; denominator: string }) {
  return (
    <div className="grid grid-cols-[minmax(150px,190px)_1fr_84px] items-center gap-4 border-b border-dotted border-rule py-3 last:border-b-0">
      <div>
        <div className="text-[14px] font-medium">{label}</div>
        <div className="mt-0.5 text-[12.5px] text-ink-soft">{denominator}</div>
      </div>
      <Meter pct={pct} tone={pct < 90 ? "bad" : pct < 99 ? "warn" : undefined} />
      <div className="figure text-right text-[20px] font-semibold">{pct.toFixed(2)}%</div>
    </div>
  );
}

/* ---------- ledger lines ---------- */

export function Line({
  label, value, tone, sub, total,
}: {
  label: ReactNode; value: ReactNode;
  tone?: "credit" | "debit"; sub?: ReactNode; total?: boolean;
}) {
  const toneCls = tone === "credit" ? "text-credit" : tone === "debit" ? "text-debit" : "";
  return (
    <div
      className={
        total
          ? "mt-1.5 flex justify-between gap-5 border-t-[1.5px] border-ink pt-3 text-[14px] font-semibold"
          : "flex justify-between gap-5 border-b border-dotted border-rule py-2.5 text-[14px] last:border-b-0"
      }
    >
      <span>
        {label}
        {sub && <span className="mt-0.5 block text-[12.5px] font-normal text-ink-soft">{sub}</span>}
      </span>
      <span className={`figure whitespace-nowrap ${toneCls}`}>{value}</span>
    </div>
  );
}

/* ---------- findings ---------- */

export function Finding({
  title, tone = "note", children,
}: { title: ReactNode; tone?: "good" | "bad" | "warn" | "note"; children: ReactNode }) {
  return (
    <div className={`finding finding-${tone} my-4`}>
      <h4 className="mb-1.5 text-[16px] font-semibold">{title}</h4>
      <p className="text-[13.5px] leading-[1.6] text-ink-soft">{children}</p>
    </div>
  );
}

/* ---------- badges ---------- */

export function Badge({
  children, tone = "neutral",
}: { children: ReactNode; tone?: "neutral" | "credit" | "debit" | "amber" | "indigo" }) {
  const cls = {
    neutral: "border-rule-strong text-ink-soft",
    credit: "border-credit text-credit",
    debit: "border-debit text-debit",
    amber: "border-amber text-amber",
    indigo: "border-indigo text-indigo",
  }[tone];
  return (
    <span className={`inline-block whitespace-nowrap rounded-[2px] border px-2 py-0.5 text-[11.5px] ${cls}`}>
      {children}
    </span>
  );
}

/** A Wilson interval drawn as a bar with the point estimate marked.
 *  Showing the interval rather than the point estimate alone is the whole
 *  argument of the verification page, so it gets a real component. */
export function IntervalBar({
  low, high, point,
}: { low: number; high: number; point: number }) {
  return (
    <div>
      <div className="relative h-[9px] w-[112px] border border-rule bg-paper-sunk">
        <i
          className="absolute -top-px -bottom-px block bg-indigo opacity-30"
          style={{ left: `${low * 100}%`, width: `${(high - low) * 100}%` }}
        />
        <b
          className="absolute -top-[3px] -bottom-[3px] w-[2px] bg-ink"
          style={{ left: `${point * 100}%` }}
        />
      </div>
      <div className="mt-0.5 font-sans text-[11px] text-ink-soft">
        {Math.round(low * 100)}–{Math.round(high * 100)}%
      </div>
    </div>
  );
}

/* ---------- states ---------- */

export function Loading({ what = "data" }: { what?: string }) {
  return (
    <div className="py-16 text-[13.5px] text-ink-soft">
      Loading {what}…
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="py-8 text-[13.5px] italic text-ink-soft">{children}</p>;
}
