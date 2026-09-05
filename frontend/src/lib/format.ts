/**
 * Formatting.
 *
 * All money is held as integer paise and formatted only at the edge. The
 * grouping is Indian (lakh/crore), so 12345678 paise renders as
 * "Rs 1,23,456.78" and not "Rs 123,456.78" — the wrong grouping is immediately
 * noticeable to the audience this is built for.
 */

export function rupees(paise: number | null | undefined): string {
  if (paise === null || paise === undefined || Number.isNaN(paise)) return "—";
  const neg = paise < 0;
  const p = Math.abs(Math.trunc(paise));
  const whole = Math.floor(p / 100);
  const sub = String(p % 100).padStart(2, "0");

  let s = String(whole);
  if (s.length > 3) {
    let head = s.slice(0, -3);
    const tail = s.slice(-3);
    const parts: string[] = [];
    while (head.length > 2) {
      parts.unshift(head.slice(-2));
      head = head.slice(0, -2);
    }
    if (head) parts.unshift(head);
    s = parts.join(",") + "," + tail;
  }
  return `${neg ? "-" : ""}Rs ${s}.${sub}`;
}

/** Compact form for chart axes, where full precision is noise. */
export function rupeesShort(paise: number): string {
  const r = Math.abs(paise) / 100;
  const sign = paise < 0 ? "-" : "";
  if (r >= 1e7) return `${sign}₹${(r / 1e7).toFixed(1)}Cr`;
  if (r >= 1e5) return `${sign}₹${(r / 1e5).toFixed(1)}L`;
  if (r >= 1e3) return `${sign}₹${(r / 1e3).toFixed(0)}k`;
  return `${sign}₹${r.toFixed(0)}`;
}

export const pct = (v: number, dp = 1) => `${(v * 100).toFixed(dp)}%`;
export const pctRaw = (v: number, dp = 2) => `${v.toFixed(dp)}%`;

export function titleCase(code: string): string {
  return code.replace(/_/g, " ").toLowerCase().replace(/^./, (c) => c.toUpperCase());
}

export function shortDate(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString("en-IN", { day: "2-digit", month: "short" });
}

/** Benign codes describe variances the engine understands and which need no
 *  action. Kept in one place so the split cannot drift between pages. */
export const BENIGN_CODES = new Set([
  "ROUNDING", "TIMING_LAG", "ON_HOLD", "NOT_YET_SETTLED",
]);

export const isBenign = (code?: string | null) => !!code && BENIGN_CODES.has(code);
