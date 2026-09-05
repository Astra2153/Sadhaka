import { useEffect, useMemo, useRef, useState } from "react";

/**
 * Scientific calculator, docked to the side of every page.
 *
 * WHY THIS EXISTS
 * ---------------
 * Every number this app shows is the output of an engine the reader has no
 * particular reason to trust yet. The honest response to "is 2% of Rs 7,666.72
 * really Rs 153.33?" is not "trust the dashboard" — it is to let them check it
 * on the spot, without leaving the page or opening a second tab and losing
 * their place.
 *
 * So the calculator is deliberately adjacent to the evidence rather than
 * hidden in a menu, and it ships with the finance constants this project
 * actually uses (MDR rates, GST, TDS, TCS) as one-tap inserts — because the
 * doubt a reader has on a reconciliation page is almost always "where did that
 * deduction come from", not "what is the sine of 40 degrees".
 *
 * IMPLEMENTATION NOTE
 * -------------------
 * The expression evaluator is a hand-written recursive-descent parser, not
 * `eval()` or `new Function()`. Those would execute arbitrary JavaScript from
 * an input box, which is a genuine injection vector even in a local tool and
 * an indefensible one in a finance app. The parser accepts only numbers,
 * operators, parentheses and a fixed whitelist of functions and constants —
 * anything else is a syntax error rather than executed code.
 */

/* ---------------------------------------------------------------------------
 * Tokeniser
 * ------------------------------------------------------------------------ */

type Token =
  | { t: "num"; v: number }
  | { t: "op"; v: string }
  | { t: "fn"; v: string }
  | { t: "lparen" }
  | { t: "rparen" };

const FUNCTIONS = new Set([
  "sin", "cos", "tan", "asin", "acos", "atan",
  "sinh", "cosh", "tanh",
  "ln", "log", "log2", "sqrt", "cbrt", "abs",
  "exp", "floor", "ceil", "round", "fact", "inv",
]);

const CONSTANTS: Record<string, number> = {
  pi: Math.PI,
  e: Math.E,
};

function tokenize(src: string): Token[] {
  const out: Token[] = [];
  let i = 0;
  const s = src.replace(/\s+/g, "");

  while (i < s.length) {
    const c = s[i];

    if (/[0-9.]/.test(c)) {
      let j = i;
      while (j < s.length && /[0-9.]/.test(s[j])) j++;
      const raw = s.slice(i, j);
      if ((raw.match(/\./g) || []).length > 1) {
        throw new Error(`Malformed number "${raw}"`);
      }
      out.push({ t: "num", v: parseFloat(raw) });
      i = j;
      continue;
    }

    if (/[a-zA-Z]/.test(c)) {
      let j = i;
      while (j < s.length && /[a-zA-Z0-9_]/.test(s[j])) j++;
      const word = s.slice(i, j).toLowerCase();
      if (word in CONSTANTS) {
        out.push({ t: "num", v: CONSTANTS[word] });
      } else if (FUNCTIONS.has(word)) {
        out.push({ t: "fn", v: word });
      } else {
        throw new Error(`Unknown name "${word}"`);
      }
      i = j;
      continue;
    }

    if ("+-*/^%".includes(c)) {
      out.push({ t: "op", v: c });
      i++;
      continue;
    }
    if (c === "(") { out.push({ t: "lparen" }); i++; continue; }
    if (c === ")") { out.push({ t: "rparen" }); i++; continue; }

    throw new Error(`Unexpected character "${c}"`);
  }
  return out;
}

/* ---------------------------------------------------------------------------
 * Recursive-descent parser
 *
 *   expr    := term (('+' | '-') term)*
 *   term    := power (('*' | '/' | '%') power)*
 *   power   := unary ('^' power)?          right-associative
 *   unary   := ('-' | '+') unary | primary
 *   primary := number | fn '(' expr ')' | '(' expr ')'
 * ------------------------------------------------------------------------ */

function parse(tokens: Token[], degrees: boolean): number {
  let pos = 0;

  const peek = () => tokens[pos];
  const eat = () => tokens[pos++];

  function expr(): number {
    let left = term();
    while (peek()?.t === "op" && "+-".includes((peek() as any).v)) {
      const op = (eat() as any).v;
      const right = term();
      left = op === "+" ? left + right : left - right;
    }
    return left;
  }

  function term(): number {
    let left = power();
    while (peek()?.t === "op" && "*/%".includes((peek() as any).v)) {
      const op = (eat() as any).v;
      const right = power();
      if (op === "*") left = left * right;
      else if (op === "/") {
        if (right === 0) throw new Error("Division by zero");
        left = left / right;
      } else {
        if (right === 0) throw new Error("Modulo by zero");
        left = left % right;
      }
    }
    return left;
  }

  function power(): number {
    const base = unary();
    if (peek()?.t === "op" && (peek() as any).v === "^") {
      eat();
      const exponent = power(); // right-associative
      return Math.pow(base, exponent);
    }
    return base;
  }

  function unary(): number {
    const p = peek();
    if (p?.t === "op" && ((p as any).v === "-" || (p as any).v === "+")) {
      const op = (eat() as any).v;
      const v = unary();
      return op === "-" ? -v : v;
    }
    return primary();
  }

  function primary(): number {
    const p = peek();
    if (!p) throw new Error("Unexpected end of expression");

    if (p.t === "num") { eat(); return (p as any).v; }

    if (p.t === "fn") {
      const name = (eat() as any).v;
      if (peek()?.t !== "lparen") throw new Error(`${name} needs parentheses`);
      eat();
      const arg = expr();
      if (peek()?.t !== "rparen") throw new Error(`Missing ")" after ${name}`);
      eat();
      return applyFn(name, arg, degrees);
    }

    if (p.t === "lparen") {
      eat();
      const v = expr();
      if (peek()?.t !== "rparen") throw new Error('Missing ")"');
      eat();
      return v;
    }

    throw new Error("Unexpected token");
  }

  const result = expr();
  if (pos < tokens.length) throw new Error("Trailing characters in expression");
  return result;
}

function applyFn(name: string, x: number, degrees: boolean): number {
  const toRad = (v: number) => (degrees ? (v * Math.PI) / 180 : v);
  const fromRad = (v: number) => (degrees ? (v * 180) / Math.PI : v);

  switch (name) {
    case "sin": return Math.sin(toRad(x));
    case "cos": return Math.cos(toRad(x));
    case "tan": return Math.tan(toRad(x));
    case "asin":
      if (x < -1 || x > 1) throw new Error("asin needs -1 to 1");
      return fromRad(Math.asin(x));
    case "acos":
      if (x < -1 || x > 1) throw new Error("acos needs -1 to 1");
      return fromRad(Math.acos(x));
    case "atan": return fromRad(Math.atan(x));
    case "sinh": return Math.sinh(x);
    case "cosh": return Math.cosh(x);
    case "tanh": return Math.tanh(x);
    case "ln":
      if (x <= 0) throw new Error("ln needs a positive number");
      return Math.log(x);
    case "log":
      if (x <= 0) throw new Error("log needs a positive number");
      return Math.log10(x);
    case "log2":
      if (x <= 0) throw new Error("log2 needs a positive number");
      return Math.log2(x);
    case "sqrt":
      if (x < 0) throw new Error("sqrt needs a non-negative number");
      return Math.sqrt(x);
    case "cbrt": return Math.cbrt(x);
    case "abs": return Math.abs(x);
    case "exp": return Math.exp(x);
    case "floor": return Math.floor(x);
    case "ceil": return Math.ceil(x);
    case "round": return Math.round(x);
    case "inv":
      if (x === 0) throw new Error("Cannot invert zero");
      return 1 / x;
    case "fact": {
      if (x < 0 || !Number.isInteger(x)) throw new Error("fact needs a whole number ≥ 0");
      if (x > 170) throw new Error("fact overflows above 170");
      let r = 1;
      for (let k = 2; k <= x; k++) r *= k;
      return r;
    }
    default: throw new Error(`Unknown function "${name}"`);
  }
}

export function evaluate(src: string, degrees: boolean): number {
  if (!src.trim()) throw new Error("Empty expression");
  return parse(tokenize(src), degrees);
}

/* ---------------------------------------------------------------------------
 * Finance presets — the constants this project actually reconciles against.
 * ------------------------------------------------------------------------ */

const PRESETS: { label: string; insert: string; hint: string }[] = [
  { label: "×2% MDR", insert: "*0.02", hint: "Standard card / netbanking / wallet MDR used in this dataset" },
  { label: "×18% GST", insert: "*0.18", hint: "GST on the gateway fee — charged on the fee only, never the transaction value" },
  { label: "×1.18", insert: "*1.18", hint: "Fee plus its GST in one step (the all-in cost of a fee)" },
  { label: "×0.1% TDS", insert: "*0.001", hint: "Section 194-O, current rate since 1 Oct 2024 (marketplace payouts only)" },
  { label: "×0.5% TCS", insert: "*0.005", hint: "Section 52 GST TCS, halved on 10 Jul 2024 (marketplace operators only)" },
  { label: "÷100 → ₹", insert: "/100", hint: "Paise to rupees — every amount is stored in paise internally" },
];

const KEYPAD: { label: string; insert?: string; action?: string; wide?: boolean }[][] = [
  [{ label: "sin", insert: "sin(" }, { label: "cos", insert: "cos(" }, { label: "tan", insert: "tan(" }, { label: "ln", insert: "ln(" }, { label: "log", insert: "log(" }],
  [{ label: "√", insert: "sqrt(" }, { label: "x²", insert: "^2" }, { label: "xʸ", insert: "^" }, { label: "1/x", insert: "inv(" }, { label: "n!", insert: "fact(" }],
  [{ label: "(", insert: "(" }, { label: ")", insert: ")" }, { label: "π", insert: "pi" }, { label: "e", insert: "e" }, { label: "%", insert: "%" }],
  [{ label: "7", insert: "7" }, { label: "8", insert: "8" }, { label: "9", insert: "9" }, { label: "÷", insert: "/" }, { label: "C", action: "clear" }],
  [{ label: "4", insert: "4" }, { label: "5", insert: "5" }, { label: "6", insert: "6" }, { label: "×", insert: "*" }, { label: "⌫", action: "back" }],
  [{ label: "1", insert: "1" }, { label: "2", insert: "2" }, { label: "3", insert: "3" }, { label: "−", insert: "-" }, { label: "ANS", action: "ans" }],
  [{ label: "0", insert: "0" }, { label: ".", insert: "." }, { label: "+", insert: "+" }, { label: "=", action: "equals", wide: true }],
];

interface HistoryItem { expr: string; result: string }

export default function Calculator({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [expr, setExpr] = useState("");
  const [result, setResult] = useState<string>("");
  const [error, setError] = useState<string>("");
  const [degrees, setDegrees] = useState(true);
  const [history, setHistory] = useState<HistoryItem[]>([]);
  const [ans, setAns] = useState<number>(0);
  const inputRef = useRef<HTMLInputElement>(null);

  /* Live preview: evaluate as the user types, but only SHOW an error once
     they commit with "=" — flashing red mid-expression while someone is
     halfway through typing "2*(" is noise, not feedback. */
  const preview = useMemo(() => {
    if (!expr.trim()) return "";
    try {
      const v = evaluate(expr, degrees);
      return formatNumber(v);
    } catch {
      return "";
    }
  }, [expr, degrees]);

  useEffect(() => {
    if (open) inputRef.current?.focus();
  }, [open]);

  function insert(text: string) {
    setExpr((e) => e + text);
    setError("");
    inputRef.current?.focus();
  }

  function compute() {
    try {
      const v = evaluate(expr, degrees);
      const formatted = formatNumber(v);
      setResult(formatted);
      setAns(v);
      setError("");
      setHistory((h) => [{ expr, result: formatted }, ...h].slice(0, 12));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Invalid expression");
      setResult("");
    }
  }

  function handleKey(action?: string, ins?: string) {
    if (ins) return insert(ins);
    if (action === "clear") { setExpr(""); setResult(""); setError(""); return; }
    if (action === "back") { setExpr((e) => e.slice(0, -1)); setError(""); return; }
    if (action === "ans") return insert(String(ans));
    if (action === "equals") return compute();
  }

  if (!open) return null;

  return (
    <aside
      className="fixed right-0 top-0 z-40 flex h-full w-[330px] flex-col border-l border-rule bg-paper shadow-[-8px_0_24px_rgba(23,41,61,0.06)]"
      aria-label="Scientific calculator"
    >
      <div className="flex items-center justify-between border-b border-ink px-4 py-3">
        <div>
          <h2 className="font-serif text-[17px] font-semibold leading-none">Calculator</h2>
          <p className="mt-1 text-[11.5px] text-ink-soft">Check any figure on the page yourself</p>
        </div>
        <button className="chip" onClick={onClose} aria-label="Close calculator">✕</button>
      </div>

      <div className="flex-1 overflow-y-auto px-4 py-3">
        {/* display */}
        <div className="border border-rule bg-paper-sunk px-3 py-2.5">
          <input
            ref={inputRef}
            className="w-full bg-transparent text-right font-serif text-[18px] outline-none"
            value={expr}
            placeholder="7666.72*0.02"
            onChange={(e) => { setExpr(e.target.value); setError(""); }}
            onKeyDown={(e) => {
              if (e.key === "Enter") { e.preventDefault(); compute(); }
              if (e.key === "Escape") onClose();
            }}
            aria-label="Expression"
          />
          <div className="mt-1.5 flex items-baseline justify-between gap-2">
            <span className="text-[10.5px] uppercase tracking-wide text-ink-soft">
              {error ? "error" : result ? "result" : preview ? "preview" : ""}
            </span>
            <span
              className={
                error
                  ? "text-right text-[12px] text-debit"
                  : "figure text-right text-[20px] " + (result ? "text-ink" : "text-ink-soft")
              }
            >
              {error || result || preview}
            </span>
          </div>
        </div>

        {/* angle mode */}
        <div className="mt-2.5 flex items-center gap-2">
          <button
            className="chip"
            aria-pressed={degrees}
            onClick={() => setDegrees(true)}
          >DEG</button>
          <button
            className="chip"
            aria-pressed={!degrees}
            onClick={() => setDegrees(false)}
          >RAD</button>
          <span className="ml-auto text-[11px] text-ink-soft">
            trig in {degrees ? "degrees" : "radians"}
          </span>
        </div>

        {/* finance presets */}
        <div className="mt-4">
          <h3 className="mb-2 text-[11.5px] font-semibold uppercase tracking-wide text-ink-soft">
            This project's rates
          </h3>
          <div className="grid grid-cols-2 gap-1.5">
            {PRESETS.map((p) => (
              <button
                key={p.label}
                className="chip text-left"
                title={p.hint}
                onClick={() => insert(p.insert)}
              >
                {p.label}
              </button>
            ))}
          </div>
          <p className="mt-2 text-[11px] leading-[1.5] text-ink-soft">
            Hover any rate to see what it is and when it applies. Type a gross
            amount, tap ×2% MDR, then ×18% GST to reproduce a deduction from
            the exceptions page.
          </p>
        </div>

        {/* keypad */}
        <div className="mt-4 space-y-1.5">
          {KEYPAD.map((row, i) => (
            <div key={i} className="grid grid-cols-5 gap-1.5">
              {row.map((k) => (
                <button
                  key={k.label}
                  className={`chip ${k.wide ? "col-span-2" : ""} ${
                    k.action === "equals" ? "!bg-ink !text-paper !border-ink" : ""
                  }`}
                  onClick={() => handleKey(k.action, k.insert)}
                >
                  {k.label}
                </button>
              ))}
            </div>
          ))}
        </div>

        {/* history */}
        {history.length > 0 && (
          <div className="mt-5">
            <div className="mb-2 flex items-center justify-between">
              <h3 className="text-[11.5px] font-semibold uppercase tracking-wide text-ink-soft">
                History
              </h3>
              <button className="text-[11px] text-ink-soft underline" onClick={() => setHistory([])}>
                clear
              </button>
            </div>
            <ul className="space-y-1.5">
              {history.map((h, i) => (
                <li key={i}>
                  <button
                    className="w-full text-left text-[12px] hover:bg-paper-sunk"
                    onClick={() => setExpr(h.expr)}
                    title="Click to reuse this expression"
                  >
                    <span className="text-ink-soft">{h.expr}</span>
                    <span className="figure float-right">{h.result}</span>
                  </button>
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>

      <div className="border-t border-rule px-4 py-2.5 text-[11px] leading-[1.5] text-ink-soft">
        Expressions are parsed, not executed — only numbers, operators and a
        fixed list of functions are accepted, so nothing typed here can run as code.
      </div>
    </aside>
  );
}

/** Enough precision to verify a paise-level figure, without printing
 *  floating-point noise like 153.33000000000001. */
function formatNumber(v: number): string {
  if (!Number.isFinite(v)) return v > 0 ? "∞" : Number.isNaN(v) ? "NaN" : "-∞";
  if (Number.isInteger(v) && Math.abs(v) < 1e15) return v.toLocaleString("en-IN");
  if (Math.abs(v) >= 1e12 || (Math.abs(v) < 1e-6 && v !== 0)) return v.toExponential(6);
  const rounded = parseFloat(v.toPrecision(12));
  return rounded.toLocaleString("en-IN", { maximumFractionDigits: 6 });
}
