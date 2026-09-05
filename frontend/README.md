# Sadhaka — Frontend

React + TypeScript + Vite + Tailwind. Eight pages over the reconciliation engine, deployable to Vercel as a static site.

## Running it

```bash
npm install
npm run dev          # http://localhost:5173
```

That is all it needs. **The app renders fully with no backend running**, because a snapshot of the last real pipeline run is bundled at `src/data/`. That is deliberate rather than a convenience: a reviewer opening the deployed link should see real numbers immediately, not a spinner pointed at a cold free-tier server.

To use live data instead, point it at a running backend:

```bash
cp .env.example .env
# then set:  VITE_API_BASE_URL=http://127.0.0.1:8000
npm run dev
```

Live data always takes precedence when reachable. The nav shows which of the two is on screen, so nobody mistakes one for the other.

```bash
npm run build        # -> dist/
npm run preview      # serve the production build locally
npm run lint         # typecheck only, no emit
```

## Deploying to Vercel

Import the repo and set **Root Directory** to this folder. `vercel.json` already sets the framework, build command, output directory, and the SPA rewrite that stops `/verification` 404-ing on a hard refresh.

Leave `VITE_API_BASE_URL` unset and Vercel serves the bundled snapshot — which is what you want unless the backend is also deployed and publicly reachable. If you do deploy the backend, set the variable in Vercel's environment settings and redeploy; nothing in the code changes.

## Pages

| Route | What it argues |
|---|---|
| `/` | The tally: banked vs settled, match rates with their denominators, where the money went, self-score against the answer key |
| `/exceptions` | Every rupee that could not be placed, split benign vs actionable, with the evidence recorded at decision time |
| `/gst` | Why ITC is a separate reconciliation, eligibility blockers named individually, effective rates per instrument |
| `/forecast` | Forward cash with a learned lag and a confidence band, and an explicit statement of what it will not predict |
| `/journal` | Double-entry postings and the trial balance, with the GST leg split from the fee |
| `/audit` | Every decision the engine made, searchable, plus entity tracing |
| `/marketplace` | Where 194-O TDS and Section 52 TCS actually apply, and the statement lag that makes correct deductions look like losses |
| `/verification` | The engine attacking itself: detection limits, confidence calibration, blind spots, counterfactuals |

## Design

The organising metaphor is a **ledger spread**, not a dashboard. The subject is double-entry reconciliation — two records that must tally — so the page is built from ruled horizontal lines and columnar alignment rather than cards with drop shadows.

- **Palette** is accounting, not product: ink blue-black `#17293D` on warm paper `#FBFAF6`, rules in `#DDD6C8`, credit green `#2C6A4E`, debit brick `#9B3A2F`. Colour carries meaning — a debit is brick because it is a debit, not because red looked good.
- **Type** pairs Spectral for figures and headings with Inter for interface chrome. Money gets a serif because on a printed statement it has weight and presence.
- **Numerals are tabular everywhere.** That is not taste; misaligned rupee columns are genuinely harder to scan for the error you are looking for.
- **No gradient hero numbers, no shadowed cards.** Neither belongs on a page a finance controller would print.

## Data flow

`src/lib/api.ts` is the only place that knows where data comes from. It tries the live API, falls back to the bundled snapshot, and reports which one it used. Every page reads from a single context populated once at the shell — eight pages each fetching independently would be slow and would let two pages disagree about the same run.

`src/types.ts` mirrors the FastAPI response models. Amounts ending `_paise` are integers in the smallest currency unit, matching Razorpay's own convention. They are never floats: binary floating point cannot represent 0.01 exactly, and a reconciliation front-end that drifts by a paise per row would undo the point of the engine behind it.

## Refreshing the bundled snapshot

After a backend run, regenerate the two JSON files so the deployed site shows current numbers:

```bash
# with the API running on :8000
curl -s localhost:8000/summary   > src/data/_summary.json     # etc.
```

In practice use the helper in the backend repo, which writes both files in one step:

```bash
python3 scripts/export_snapshot.py --out ../frontend/src/data
```

## Known limitations

- Entity tracing falls back to searching the bundled decision log when no API is configured. That log holds the most recent 600 decisions, so a trace can legitimately come up empty offline while succeeding against a live backend. The UI says which mode it is in rather than implying the entity does not exist.
- The marketplace page always reads from the snapshot, because that scenario is generated on demand rather than stored against a pipeline run.
- Charts render through Recharts, which ships around 110 kB gzipped. It is split into its own chunk so pages without charts do not pay for it.
