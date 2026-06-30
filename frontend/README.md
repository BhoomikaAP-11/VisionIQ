# BEL Frontend

Vite + React UI for the BEL Business Intelligence Copilot.

## Run

```bash
cd frontend
npm install
npm run dev
```

Open <http://localhost:5173>. Vite proxies `/api` to `http://localhost:8000`, so start the backend first:

```bash
# from the project root, in another terminal
uvicorn backend.main:app --reload --port 8000
```

## Production build

```bash
npm run build
npm run preview
```

To point the build at a non-localhost backend, set `VITE_API_BASE`:

```bash
VITE_API_BASE=https://api.example.com npm run build
```

## Structure

```
src/
├── main.jsx                    # entry, router setup
├── App.jsx                     # shell + routes
├── api.js                      # fetch wrapper for /api/*
├── styles.css                  # global tokens + components
├── pages/
│   ├── Upload.jsx              # drag-and-drop, kicks off profiling
│   └── Dashboard.jsx           # spec renderer + NL query box
└── components/
    ├── KpiCard.jsx             # KPI tiles with trend deltas
    ├── ChartCard.jsx           # Recharts dispatcher: line/bar/pareto/histogram/heatmap/forecast/anomaly
    ├── InsightsPanel.jsx       # insights + recommendations columns
    └── QualityPanel.jsx        # data quality summary
```
