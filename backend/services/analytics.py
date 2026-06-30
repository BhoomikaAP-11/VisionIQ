"""
Advanced analytics (Master Prompt Phase 13).

All functions take a pandas DataFrame plus column names and return
JSON-serialisable dicts ready for the dashboard layer. Functions degrade
gracefully when prerequisites are missing — they never raise on bad input,
they return an empty/explanatory dict instead.
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

from .profiling import safe_val, records


# ---------------------------------------------------------------------------
# KPI calculation
# ---------------------------------------------------------------------------
def kpi_summary(df: pd.DataFrame, measures: list[str], date_col: Optional[str] = None) -> list[dict]:
    """
    Build KPI cards for each measure. Computes total, period-over-period
    growth (if a date column is supplied), and a sparkline series.
    """
    kpis: list[dict] = []
    for m in measures[:6]:
        if m not in df.columns:
            continue
        s = pd.to_numeric(df[m], errors="coerce").dropna()
        if not len(s):
            continue
        total = float(s.sum())
        avg = float(s.mean())
        kpi = {
            "name": m,
            "value": round(total, 2),
            "avg": round(avg, 2),
            "count": int(s.count()),
            "trend": "stable",
            "change_pct": None,
            "sparkline": [],
        }
        if date_col and date_col in df.columns:
            tmp = df[[date_col, m]].copy()
            tmp[date_col] = pd.to_datetime(tmp[date_col], errors="coerce")
            tmp = tmp.dropna(subset=[date_col])
            if len(tmp):
                grouped = (
                    tmp.set_index(date_col)[m]
                    .resample("MS")
                    .sum()
                    .dropna()
                )
                if len(grouped) >= 2:
                    prev, curr = float(grouped.iloc[-2]), float(grouped.iloc[-1])
                    if prev != 0:
                        change = (curr - prev) / abs(prev) * 100
                        kpi["change_pct"] = round(change, 2)
                        kpi["trend"] = "up" if change > 1 else "down" if change < -1 else "stable"
                kpi["sparkline"] = [
                    {"x": str(idx.date()), "y": safe_val(val)}
                    for idx, val in grouped.tail(12).items()
                ]
        kpis.append(kpi)
    return kpis


# ---------------------------------------------------------------------------
# Trend / time-series
# ---------------------------------------------------------------------------
def trend(df: pd.DataFrame, date_col: str, measure: str, freq: str = "MS") -> dict:
    if date_col not in df.columns or measure not in df.columns:
        return {"series": [], "note": "missing columns"}
    tmp = df[[date_col, measure]].copy()
    tmp[date_col] = pd.to_datetime(tmp[date_col], errors="coerce")
    tmp[measure] = pd.to_numeric(tmp[measure], errors="coerce")
    tmp = tmp.dropna()
    if not len(tmp):
        return {"series": [], "note": "no valid data"}
    series = tmp.set_index(date_col)[measure].resample(freq).sum().dropna()

    rolling = series.rolling(window=3, min_periods=1).mean()
    return {
        "measure": measure,
        "date_col": date_col,
        "freq": freq,
        "series": [
            {"x": str(idx.date()), "y": safe_val(v), "moving_avg": safe_val(ma)}
            for (idx, v), ma in zip(series.items(), rolling)
        ],
    }


# ---------------------------------------------------------------------------
# Forecast — Holt-Winters via statsmodels, with a naive fallback
# ---------------------------------------------------------------------------
def forecast(df: pd.DataFrame, date_col: str, measure: str, periods: int = 6, freq: str = "MS") -> dict:
    """
    Forecast `periods` ahead. Trains several candidate models on a holdout,
    picks the one with the lowest MAPE, and reports accuracy alongside the
    projection so the dashboard can show how trustworthy the forecast is.
    """
    if date_col not in df.columns or measure not in df.columns:
        return {"forecast": [], "note": "missing columns"}
    tmp = df[[date_col, measure]].copy()
    tmp[date_col] = pd.to_datetime(tmp[date_col], errors="coerce")
    tmp[measure] = pd.to_numeric(tmp[measure], errors="coerce")
    tmp = tmp.dropna()
    if len(tmp) < 4:
        return {"forecast": [], "note": "not enough data points"}

    series = tmp.set_index(date_col)[measure].resample(freq).sum().dropna()
    n = len(series)
    if n < 4:
        return {"forecast": [], "note": "not enough periods"}

    # ------------------------------------------------------------------
    # Train/test split for model selection (80/20, min 1 test point)
    # ------------------------------------------------------------------
    split = max(n - max(1, n // 5), 3)
    train, test = series.iloc[:split], series.iloc[split:]
    series_var = float(series.var() or 0)
    flat_tol = max(1e-6, series_var * 1e-4)  # reject outputs with variance below this

    candidates = _candidate_models(train, len(test))
    scored: list[dict] = []

    for name, fitter in candidates.items():
        try:
            fitted = fitter(train)
            preds = fitted["forecast_fn"](len(test))
            if preds is None or len(preds) != len(test):
                continue
            mae = _mae(test.values, preds.values)
            mape = _mape(test.values, preds.values)
            # Refit on full series for actual forecast
            full_fitted = fitter(series)
            fc_values = full_fitted["forecast_fn"](periods)
            if fc_values is None or len(fc_values) != periods:
                continue
            fc_variance = float(pd.Series(fc_values).var() or 0)
            is_flat = fc_variance < flat_tol and series_var > flat_tol
            scored.append({
                "name": name,
                "mae": mae,
                "mape": mape,
                "is_flat": is_flat,
                "fc": fc_values,
            })
        except Exception:
            continue

    # Pick the model with the lowest MAE that ISN'T producing flat output
    # (unless the original series itself is flat, in which case flat is correct).
    non_flat = [m for m in scored if not m["is_flat"]]
    pool = non_flat if non_flat else scored
    if pool:
        # Tiny tie-breaker preference for Holt-Winters over naive when scores are close
        pool.sort(key=lambda m: (m["mae"], 0 if "holt" in m["name"] else 1))
        winner = pool[0]
        best_name = winner["name"]
        best_mae = winner["mae"]
        best_mape = winner["mape"]
        best_fc_full = winner["fc"]
    else:
        # Last-resort linear extrapolation
        diffs = series.diff().dropna()
        step = float(diffs.mean()) if len(diffs) else 0.0
        last = float(series.iloc[-1])
        future_idx = pd.date_range(series.index[-1], periods=periods + 1, freq=freq)[1:]
        best_fc_full = pd.Series([last + step * (i + 1) for i in range(periods)], index=future_idx)
        best_name, best_mae, best_mape = "naive-linear", float("inf"), float("inf")

    # Surface readable accuracy
    accuracy = {
        "mae": round(best_mae, 2) if best_mae != float("inf") else None,
        "mape_pct": round(best_mape, 2) if best_mape != float("inf") else None,
        "evaluation": "80/20 holdout backtest",
        "rating": _accuracy_rating(best_mape, best_mae, series.mean()),
    }

    return {
        "measure": measure,
        "method": best_name,
        "accuracy": accuracy,
        "history": [{"x": str(i.date()), "y": safe_val(v)} for i, v in series.items()],
        "forecast": [{"x": str(i.date()), "y": safe_val(v)} for i, v in best_fc_full.items()],
    }


def _mae(actual, predicted) -> float:
    import numpy as np
    a, p = np.asarray(actual, dtype=float), np.asarray(predicted, dtype=float)
    return float(np.mean(np.abs(a - p)))


def _candidate_models(series, _: int) -> dict:
    """Return a dict of {name: fitter}. fitter returns {forecast_fn}."""
    from statsmodels.tsa.holtwinters import ExponentialSmoothing, SimpleExpSmoothing

    seasonal_periods = 12 if len(series) >= 24 else None

    def _hw_add_add(s):
        m = ExponentialSmoothing(s, trend="add", seasonal="add" if seasonal_periods else None,
                                 seasonal_periods=seasonal_periods,
                                 initialization_method="estimated").fit()
        return {"forecast_fn": m.forecast}

    def _hw_add_none(s):
        m = ExponentialSmoothing(s, trend="add", seasonal=None,
                                 initialization_method="estimated").fit()
        return {"forecast_fn": m.forecast}

    def _ses(s):
        m = SimpleExpSmoothing(s, initialization_method="estimated").fit()
        return {"forecast_fn": m.forecast}

    def _seasonal_naive(s):
        period = seasonal_periods or 1
        last_season = s.iloc[-period:]
        def fc(k):
            import pandas as pd
            future_idx = pd.date_range(s.index[-1], periods=k + 1,
                                        freq=s.index.freq or "MS")[1:]
            vals = [last_season.iloc[i % period] for i in range(k)]
            return pd.Series(vals, index=future_idx)
        return {"forecast_fn": fc}

    return {
        "holt-winters-add-add": _hw_add_add,
        "holt-winters-add": _hw_add_none,
        "simple-exp-smoothing": _ses,
        "seasonal-naive": _seasonal_naive,
    }


def _mape(actual, predicted) -> float:
    import numpy as np
    a, p = np.asarray(actual, dtype=float), np.asarray(predicted, dtype=float)
    mask = a != 0
    if not mask.any():
        return float("inf")
    return float(np.mean(np.abs((a[mask] - p[mask]) / a[mask])) * 100)


def _accuracy_rating(mape: float, mae: float = None, series_mean: float = None) -> str:
    """Use MAPE when defined, else relative-MAE against the series mean."""
    if mape is not None and mape != float("inf"):
        if mape < 10:
            return "excellent"
        if mape < 20:
            return "good"
        if mape < 50:
            return "acceptable"
        return "poor"
    if mae is not None and series_mean and series_mean != 0:
        rel = abs(mae / series_mean) * 100
        if rel < 10: return "excellent"
        if rel < 20: return "good"
        if rel < 50: return "acceptable"
        return "poor"
    return "unknown"


# ---------------------------------------------------------------------------
# Correlation
# ---------------------------------------------------------------------------
def correlation_matrix(df: pd.DataFrame, measures: list[str]) -> dict:
    measures = [m for m in measures if m in df.columns]
    if len(measures) < 2:
        return {"matrix": [], "columns": measures, "note": "need >=2 numeric columns"}
    sub = df[measures].apply(pd.to_numeric, errors="coerce").dropna()
    if not len(sub):
        return {"matrix": [], "columns": measures, "note": "no overlapping data"}
    corr = sub.corr().round(3)
    return {
        "columns": measures,
        "matrix": [[safe_val(v) for v in row] for row in corr.values],
        "strong_pairs": _strong_pairs(corr),
    }


def _strong_pairs(corr: pd.DataFrame, threshold: float = 0.6) -> list[dict]:
    pairs = []
    cols = corr.columns.tolist()
    for i, a in enumerate(cols):
        for b in cols[i + 1:]:
            v = float(corr.loc[a, b])
            if abs(v) >= threshold and not np.isnan(v):
                pairs.append({"a": a, "b": b, "r": round(v, 3)})
    return sorted(pairs, key=lambda p: abs(p["r"]), reverse=True)


# ---------------------------------------------------------------------------
# Anomaly / outlier detection
# ---------------------------------------------------------------------------
def anomalies(df: pd.DataFrame, measure: str, date_col: Optional[str] = None) -> dict:
    if measure not in df.columns:
        return {"anomalies": [], "note": "missing measure"}
    s = pd.to_numeric(df[measure], errors="coerce")
    valid = s.dropna()
    if len(valid) < 5:
        return {"anomalies": [], "note": "not enough data"}
    mean, std = float(valid.mean()), float(valid.std())
    if std == 0:
        return {"anomalies": [], "note": "zero variance"}
    z = (valid - mean) / std
    flagged = valid[abs(z) > 3]
    out = []
    for idx, val in flagged.items():
        item = {"index": int(idx), "value": safe_val(val), "z": round(float(z.loc[idx]), 2)}
        if date_col and date_col in df.columns:
            item["date"] = safe_val(df.loc[idx, date_col])
        out.append(item)
    return {
        "measure": measure,
        "mean": round(mean, 2),
        "std": round(std, 2),
        "threshold_z": 3.0,
        "count": len(out),
        "anomalies": out[:50],
    }


# ---------------------------------------------------------------------------
# Top N / Bottom N
# ---------------------------------------------------------------------------
def top_n(df: pd.DataFrame, dimension: str, measure: str, n: int = 10, ascending: bool = False) -> list[dict]:
    if dimension not in df.columns or measure not in df.columns:
        return []
    tmp = df[[dimension, measure]].copy()
    tmp[measure] = pd.to_numeric(tmp[measure], errors="coerce")
    grouped = (
        tmp.dropna()
        .groupby(dimension, dropna=False)[measure]
        .sum()
        .sort_values(ascending=ascending)
        .head(n)
        .reset_index()
    )
    return records(grouped)


# ---------------------------------------------------------------------------
# Pareto / ABC
# ---------------------------------------------------------------------------
def pareto(df: pd.DataFrame, dimension: str, measure: str) -> dict:
    if dimension not in df.columns or measure not in df.columns:
        return {"items": [], "note": "missing columns"}
    tmp = df[[dimension, measure]].copy()
    tmp[measure] = pd.to_numeric(tmp[measure], errors="coerce")
    grouped = (
        tmp.dropna()
        .groupby(dimension, dropna=False)[measure]
        .sum()
        .sort_values(ascending=False)
        .reset_index()
    )
    total = float(grouped[measure].sum())
    if total <= 0:
        return {"items": [], "note": "zero total"}
    grouped["cum_pct"] = grouped[measure].cumsum() / total * 100
    grouped["abc"] = pd.cut(
        grouped["cum_pct"], bins=[-0.01, 70, 90, 100], labels=["A", "B", "C"]
    ).astype(str)
    return {
        "dimension": dimension,
        "measure": measure,
        "total": round(total, 2),
        "items": records(grouped.head(50)),
        "top_80pct_count": int((grouped["cum_pct"] <= 80).sum()),
    }


# ---------------------------------------------------------------------------
# Distribution
# ---------------------------------------------------------------------------
def histogram(df: pd.DataFrame, measure: str, bins: int = 20) -> dict:
    if measure not in df.columns:
        return {"bins": [], "note": "missing measure"}
    s = pd.to_numeric(df[measure], errors="coerce").dropna()
    if not len(s):
        return {"bins": [], "note": "no valid data"}
    counts, edges = np.histogram(s, bins=bins)
    return {
        "measure": measure,
        "bins": [
            {"range": [safe_val(edges[i]), safe_val(edges[i + 1])], "count": int(counts[i])}
            for i in range(len(counts))
        ],
    }
