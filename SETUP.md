# Setup — exact commands

Windows PowerShell. Everything runs locally; there is no database to provision and no API key needed for any of it.

```
Sadhaka/
├── Backend/          Python engine + FastAPI
├── frontend/         React + Vite (deploys to Vercel)
├── README.md         the project write-up (this is what judges read)
└── SETUP.md          this file
```

---

## 1. Backend — first run

```powershell
cd C:\Users\ASHMIT\OneDrive\Documents\PROJECT\Sadhaka\Backend

python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

If PowerShell blocks the activate script, run this once and try again:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

Then generate data and reconcile:

```powershell
python src\generate_data.py
python src\run_pipeline.py
```

You should see the reconciliation report print: throughput, four match rates with their denominators, the exception split, the GST/ITC position, the cash forecast, the journal entries and trial balance, and the self-score against the answer key.

`run.sh` does all of the above in one step but is a bash script. On Windows just run the Python commands directly, or use Git Bash if you have it.

## 2. Backend — the rest

```powershell
python src\test_suite.py                      # 68 assertions
python src\test_robustness.py --seeds 15      # 15 independent datasets
python src\marketplace_scenario.py            # 194-O TDS and Section 52 TCS
python src\run_verification.py --quick        # adversarial harness, ~1 min
python src\run_verification.py --thorough     # establishes detection floors, slow
```

Run `--thorough` at least once before recording the video. It is the profile that produces the real detection floors and finds the blind spots; `--quick` honestly reports that it cannot establish them.

## 3. Backend — API + Swagger

```powershell
uvicorn api.main:app --reload --port 8000
```

- Swagger UI: http://127.0.0.1:8000/docs
- Health check: http://127.0.0.1:8000/health

Leave this running in its own terminal if you want the frontend on live data.

## 4. Frontend

```powershell
cd C:\Users\ASHMIT\OneDrive\Documents\PROJECT\Sadhaka\frontend

npm install
npm run dev
```

Open http://localhost:5173

**It works with the backend switched off.** A snapshot of the last real pipeline run is bundled in `src/data/`, so the app renders real numbers immediately. That is deliberate: the demo must not depend on a server being awake.

To use live data instead:

```powershell
Copy-Item .env.example .env
# edit .env and set:  VITE_API_BASE_URL=http://127.0.0.1:8000
npm run dev
```

The nav shows `live API` or `bundled snapshot` so you always know which is on screen.

## 5. Refreshing the bundled snapshot

After any backend run whose numbers should appear on the deployed site:

```powershell
cd Backend
python scripts\export_snapshot.py --out ..\frontend\src\data
cd ..\frontend
npm run build
```

This calls the same endpoints the live frontend calls, so the snapshot cannot drift into a different shape from live data.

## 6. Deploying the frontend to Vercel

```powershell
cd frontend
npm install -g vercel
vercel
```

Or through the dashboard: import the repo and set **Root Directory** to `frontend`. `vercel.json` already sets the framework, build command, output directory, and the SPA rewrite that stops `/verification` returning 404 on a hard refresh.

Leave `VITE_API_BASE_URL` unset on Vercel unless the backend is also deployed and publicly reachable — otherwise the site will try a server it cannot reach, wait for the timeout, and only then fall back. Unset is faster and shows the same numbers.

## 7. Git

```powershell
cd C:\Users\ASHMIT\OneDrive\Documents\PROJECT\Sadhaka
git init
git add .
git commit -m "Sadhaka: settlement reconciliation with an adversarial verification harness"
git branch -M main
git remote add origin https://github.com/<you>/sadhaka.git
git push -u origin main
```

`.gitignore` already excludes `node_modules/`, `dist/`, `__pycache__/`, `.env` and the SQLite audit database. The generated CSVs in `Backend/data/` **are** committed on purpose — a reviewer should be able to clone and run the pipeline without generating data first.

---

## Order to do things in before recording

1. `python src\generate_data.py` then `python src\run_pipeline.py` — confirm it prints cleanly
2. `python src\test_suite.py` — confirm 68/68
3. `python src\run_verification.py --thorough` — this is the centrepiece; let it finish
4. `python scripts\export_snapshot.py --out ..\frontend\src\data` — so the UI shows those results
5. `npm run dev` in `frontend`, click through all eight pages
6. Record

## Troubleshooting

**`ModuleNotFoundError: No module named 'config'`** — run scripts from inside `Backend/`, not from the repo root. The scripts add `src/` to the path relative to their own location.

**`FileNotFoundError: orders.csv not found`** — run `python src\generate_data.py` first.

**API returns 503 "No audit trail found"** — run `python src\run_pipeline.py` before starting uvicorn.

**`/verification` returns 503** — run `python src\run_verification.py` first; the report is a separate artifact from the pipeline.

**Frontend shows an amber banner about an unreachable API** — expected when `VITE_API_BASE_URL` is set but uvicorn is not running. Either start the backend or unset the variable.
