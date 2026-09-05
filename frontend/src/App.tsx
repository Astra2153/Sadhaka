import { useEffect, useState, createContext, useContext, lazy, Suspense } from "react";
import { NavLink, Route, Routes, Navigate } from "react-router-dom";
import { loadBundle, type DataSource, API_BASE } from "@/lib/api";
import type { Bundle } from "@/types";
import { Loading } from "@/components/ui";
import Calculator from "@/components/Calculator";
import FirstRunGuide from "@/components/FirstRunGuide";

/* Pages are code-split. The overview is the common entry point and the
   chart-heavy pages pull in Recharts, so loading all eight upfront would make
   the first paint wait on code most visitors never reach. */
const Overview = lazy(() => import("@/pages/Overview"));
const Exceptions = lazy(() => import("@/pages/Exceptions"));
const Gst = lazy(() => import("@/pages/Gst"));
const Forecast = lazy(() => import("@/pages/Forecast"));
const Journal = lazy(() => import("@/pages/Journal"));
const Verification = lazy(() => import("@/pages/Verification"));
const Ask = lazy(() => import("@/pages/Ask"));
const ApiDocs = lazy(() => import("@/pages/ApiDocs"));
const Ledger = lazy(() => import("@/pages/Ledger"));
const Admin = lazy(() => import("@/pages/Admin"));
const AuditTrail = lazy(() => import("@/pages/AuditTrail"));
const Marketplace = lazy(() => import("@/pages/Marketplace"));

/* Bundle is loaded once at the shell and shared, rather than each page
   fetching for itself. Seven pages each firing eight requests would be slow
   and would let two pages disagree about the same run. */
const DataCtx = createContext<{ data: Bundle; source: DataSource } | null>(null);

export function useData() {
  const ctx = useContext(DataCtx);
  if (!ctx) throw new Error("useData called outside the provider");
  return ctx;
}

const NAV = [
  { to: "/", label: "Overview", end: true },
  { to: "/exceptions", label: "Exceptions" },
  { to: "/gst", label: "GST & ITC" },
  { to: "/forecast", label: "Cash forecast" },
  { to: "/journal", label: "Journal" },
  { to: "/audit", label: "Audit trail" },
  { to: "/marketplace", label: "Marketplace" },
  { to: "/verification", label: "Verification" },
  { to: "/ask", label: "Ask" },
  { to: "/ledger", label: "Ledger" },
  { to: "/api", label: "API" },
  { to: "/admin", label: "Admin" },
];

export default function App() {
  const [state, setState] = useState<{ data: Bundle; source: DataSource } | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [calcOpen, setCalcOpen] = useState(false);

  /* Alt+C toggles the calculator. Chosen over Ctrl/Cmd+K because that is
     conventionally a command palette, and over a bare key because this app
     has text inputs on three pages. */
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.altKey && e.key.toLowerCase() === "c") {
        e.preventDefault();
        setCalcOpen((o) => !o);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  useEffect(() => {
    let cancelled = false;
    loadBundle().then((r) => {
      if (cancelled) return;
      setState({ data: r.data, source: r.source });
      if (r.source === "snapshot" && API_BASE) {
        setNote(`The API at ${API_BASE} is not reachable, so this is the bundled result of the last pipeline run.`);
      }
    });
    return () => { cancelled = true; };
  }, []);

  if (!state) {
    return (
      <div className="wrap">
        <Loading what="the reconciliation run" />
      </div>
    );
  }

  return (
    <DataCtx.Provider value={state}>
      <div className="wrap pb-24">
        <nav className="flex flex-wrap items-center gap-x-6 gap-y-2 border-b border-rule py-4 text-[13px]">
          <span className="font-serif text-[17px] font-semibold">
            Sadhaka<span className="ml-1.5 text-[12px] font-normal text-ink-soft">साधक</span>
          </span>
          {NAV.map((n) => (
            <NavLink
              key={n.to}
              to={n.to}
              end={n.end}
              className={({ isActive }) =>
                isActive
                  ? "border-b-2 border-ink pb-0.5 font-medium text-ink"
                  : "border-b-2 border-transparent pb-0.5 text-ink-soft hover:text-ink"
              }
            >
              {n.label}
            </NavLink>
          ))}
          <span className="ml-auto text-[11.5px] text-ink-soft">
            {state.source === "live" ? "live API" : "bundled snapshot"}
          </span>
          <button
            className="chip"
            onClick={() => setCalcOpen((o) => !o)}
            aria-pressed={calcOpen}
            title="Scientific calculator — check any figure yourself (Alt+C)"
          >
            Calculator
          </button>
        </nav>

        {note && (
          <p className="border-b border-rule py-2 text-[12.5px] text-amber">{note}</p>
        )}

        <FirstRunGuide />

        <Suspense fallback={<Loading what="this view" />}>
        <div className="animate-fade">
        <Routes>
          <Route path="/" element={<Overview />} />
          <Route path="/exceptions" element={<Exceptions />} />
          <Route path="/gst" element={<Gst />} />
          <Route path="/forecast" element={<Forecast />} />
          <Route path="/journal" element={<Journal />} />
          <Route path="/audit" element={<AuditTrail />} />
          <Route path="/marketplace" element={<Marketplace />} />
          <Route path="/verification" element={<Verification />} />
          <Route path="/ask" element={<Ask />} />
          <Route path="/api" element={<ApiDocs />} />
          <Route path="/ledger" element={<Ledger />} />
          <Route path="/admin" element={<Admin />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
        </div>
        </Suspense>

        <Calculator open={calcOpen} onClose={() => setCalcOpen(false)} />

        <footer className="max-w-[82ch] py-8 text-[12.5px] leading-[1.7] text-ink-soft">
          All data is synthetic, generated to mirror Razorpay's real settlement
          recon schema. Amounts are held in paise internally, as the Razorpay
          API does, and formatted only on display. Every figure shown is read
          from the engine's audit trail — nothing is recomputed for the screen.
        </footer>
      </div>
    </DataCtx.Provider>
  );
}
