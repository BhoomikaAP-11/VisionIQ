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
                # Detect whether the LAST monthly bucket is incomplete.
                # If the max raw date is before the end of that month, the sum
                # is under-reported and would produce a fake decline.
                complete_grouped = grouped
                last_period_partial = False
                if len(grouped):
                    max_date = tmp[date_col].max()
                    last_bucket_start = grouped.index[-1]
                    last_bucket_end = (last_bucket_start
                                        + pd.offsets.MonthEnd(0)).normalize()
                    if pd.notna(max_date) and max_date.normalize() < last_bucket_end:
                        last_period_partial = True
                        complete_grouped = grouped.iloc[:-1]

                if len(complete_grouped) >= 2:
                    prev = float(complete_grouped.iloc[-2])
                    curr = float(complete_grouped.iloc[-1])
                    if prev != 0:
                        change = (curr - prev) / abs(prev) * 100
                        curr_label = complete_grouped.index[-1].strftime('%b %Y')
                        prev_label = complete_grouped.index[-2].strftime('%b %Y')
                        kpi["change_pct"] = round(change, 2)
                        kpi["change_abs"] = round(curr - prev, 2)
                        kpi["current_value"] = round(curr, 2)
                        kpi["previous_value"] = round(prev, 2)
                        kpi["trend"] = "up" if change > 1 else "down" if change < -1 else "stable"
                        kpi["compare_period_label"] = f"{curr_label} vs {prev_label}"
                        kpi["compare_current_period"] = curr_label
                        kpi["compare_previous_period"] = prev_label
                        if last_period_partial:
                            kpi["note"] = (
                                f"Latest bucket ({grouped.index[-1].strftime('%b %Y')}) "
                                "is partial — excluded from % change."
                            )
                kpi["sparkline"] = [
                    {"x": str(idx.date()), "y": safe_val(val),
                     "partial": bool(last_period_partial and idx == grouped.index[-1])}
                    for idx, val in grouped.tail(12).items()
                ]
                kpi["last_period_partial"] = last_period_partial
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

    # Detect partial last bucket for annotation
    max_date = tmp[date_col].max()
    last_bucket_end = (series.index[-1] + pd.offsets.MonthEnd(0)).normalize() if len(series) else None
    last_partial = bool(last_bucket_end is not None and pd.notna(max_date) and max_date.normalize() < last_bucket_end)

    return {
        "measure": measure,
        "date_col": date_col,
        "freq": freq,
        "last_period_partial": last_partial,
        "series": [
            {
                "x": str(idx.date()),
                "y": safe_val(v),
                "moving_avg": safe_val(ma),
                "partial": bool(last_partial and idx == series.index[-1]),
            }
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

    # Drop the last bucket if it's incomplete — a half-month drags training
    # toward a false decline.
    if len(series):
        max_date = tmp[date_col].max()
        last_end = (series.index[-1] + pd.offsets.MonthEnd(0)).normalize()
        if pd.notna(max_date) and max_date.normalize() < last_end:
            series = series.iloc[:-1]

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
        # Tie-breaker: prefer damped over pure additive-trend, then any
        # Holt-Winters over naive baselines, then lower MAE wins.
        def _rank(m):
            name = m["name"]
            bias = 0
            if "damped" in name: bias = -0.05
            elif "notrend" in name: bias = -0.03
            elif "holt" in name: bias = -0.02
            return (m["mae"] * (1 + bias), name)
        pool.sort(key=_rank)
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

    # 95% prediction band derived from in-sample residual σ.
    # Widens by √h as forecast horizon extends (standard for AR/HW).
    try:
        import numpy as np
        # Rebuild residuals on the full-series fit
        winning_fitter = candidates[best_name]
        full_fit = winning_fitter(series)
        # Recompute in-sample fitted values by running forecast_fn against
        # a rolling window is expensive; use the residual σ from backtest
        # as a proxy.
        resid_sigma = float(best_mae * 1.25) if best_mae != float("inf") else float(series.std() * 0.5)
    except Exception:
        resid_sigma = float(series.std() * 0.5)
    z = 1.96  # ~95%
    forecast_points = []
    for i, (idx, v) in enumerate(best_fc_full.items(), start=1):
        band = z * resid_sigma * (i ** 0.5)
        forecast_points.append({
            "x": str(idx.date()),
            "y": safe_val(v),
            "y_lo": safe_val(v - band),
            "y_hi": safe_val(v + band),
        })

    return {
        "measure": measure,
        "method": best_name,
        "accuracy": accuracy,
        "history": [{"x": str(i.date()), "y": safe_val(v)} for i, v in series.items()],
        "forecast": forecast_points,
    }


def _mae(actual, predicted) -> float:
    import numpy as np
    a, p = np.asarray(actual, dtype=float), np.asarray(predicted, dtype=float)
    return float(np.mean(np.abs(a - p)))


def _candidate_models(series, _: int) -> dict:
    """
    Candidate forecasters. Damped-trend + no-trend variants are included
    because on short series pure additive trend extrapolates a straight-line
    decline (or rise) that keeps compounding — a spec violation in practice.
    """
    from statsmodels.tsa.holtwinters import ExponentialSmoothing, SimpleExpSmoothing

    seasonal_periods = 12 if len(series) >= 24 else None
    short = len(series) < 12

    def _hw_add_add(s):
        m = ExponentialSmoothing(s, trend="add",
                                 seasonal="add" if seasonal_periods else None,
                                 seasonal_periods=seasonal_periods,
                                 initialization_method="estimated").fit()
        return {"forecast_fn": m.forecast}

    def _hw_add_none(s):
        m = ExponentialSmoothing(s, trend="add", seasonal=None,
                                 initialization_method="estimated").fit()
        return {"forecast_fn": m.forecast}

    def _hw_damped(s):
        m = ExponentialSmoothing(s, trend="add", damped_trend=True, seasonal=None,
                                 initialization_method="estimated").fit()
        return {"forecast_fn": m.forecast}

    def _hw_notrend(s):
        m = ExponentialSmoothing(s, trend=None, seasonal=None,
                                 initialization_method="estimated").fit()
        return {"forecast_fn": m.forecast}

    def _ses(s):
        m = SimpleExpSmoothing(s, initialization_method="estimated").fit()
        return {"forecast_fn": m.forecast}

    def _mean(s):
        """Constant-mean baseline. Useful on noisy short series."""
        avg = float(s.mean())
        def fc(k):
            import pandas as pd
            idx = pd.date_range(s.index[-1], periods=k + 1,
                                 freq=s.index.freq or "MS")[1:]
            return pd.Series([avg] * k, index=idx)
        return {"forecast_fn": fc}

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

    candidates = {
        "holt-winters-damped": _hw_damped,   # PREFER on short series
        "holt-winters-add": _hw_add_none,
        "holt-winters-notrend": _hw_notrend,
        "holt-winters-add-add": _hw_add_add,
        "simple-exp-smoothing": _ses,
        "mean-baseline": _mean,
        "seasonal-naive": _seasonal_naive,
    }
    # On very short series (< 12 months), drop the aggressive-trend model
    if short:
        candidates.pop("holt-winters-add-add", None)
    return candidates


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
def correlation_matrix(df: pd.DataFrame, measures: list[str],
                         date_col: str | None = None,
                         dimension: str | None = None) -> dict:
    """
    Correlation between measures. Reports THREE levels:
      - row-level:       raw pairwise correlation (existing behaviour)
      - aggregate-level: correlation of sums grouped by dimension (if given)
      - period-level:    correlation of monthly sums (if date_col given)
    The aggregate views are what a business user actually wants ("do more
    boxes shipped tend to mean more revenue?"). Row-level often shows near-
    zero when unit prices vary between products.
    """
    measures = [m for m in measures if m in df.columns]
    if len(measures) < 2:
        return {"matrix": [], "columns": measures, "note": "need >=2 numeric columns"}
    sub = df[measures].apply(pd.to_numeric, errors="coerce").dropna()
    if not len(sub):
        return {"matrix": [], "columns": measures, "note": "no overlapping data"}
    row_corr = sub.corr().round(3)

    aggregate_corr = None
    period_corr = None
    strong_pairs = _strong_pairs(row_corr)

    # Aggregate by a dimension (e.g. by Product)
    if dimension and dimension in df.columns:
        try:
            agg = (df[[dimension, *measures]]
                   .apply(lambda s: pd.to_numeric(s, errors="ignore"))
                   .groupby(dimension)[measures].sum()
                   .dropna())
            if len(agg) >= 3:
                aggregate_corr = agg.corr().round(3)
                strong_pairs = _strong_pairs(aggregate_corr) or strong_pairs
        except Exception:
            pass

    # Aggregate by time (monthly sums)
    if date_col and date_col in df.columns:
        try:
            tmp = df[[date_col, *measures]].copy()
            tmp[date_col] = pd.to_datetime(tmp[date_col], errors="coerce")
            for m in measures:
                tmp[m] = pd.to_numeric(tmp[m], errors="coerce")
            tmp = tmp.dropna()
            monthly = tmp.set_index(date_col)[measures].resample("MS").sum().dropna()
            if len(monthly) >= 3:
                period_corr = monthly.corr().round(3)
        except Exception:
            pass

    # Prefer the most informative level for the headline matrix
    headline = aggregate_corr if aggregate_corr is not None else period_corr if period_corr is not None else row_corr
    level = "by-" + (dimension if aggregate_corr is not None else "month" if period_corr is not None else "row")

    return {
        "columns": measures,
        "level": level,
        "matrix": [[safe_val(v) for v in row] for row in headline.values],
        "row_level_matrix": [[safe_val(v) for v in row] for row in row_corr.values],
        "aggregate_by": dimension if aggregate_corr is not None else None,
        "strong_pairs": _strong_pairs(headline),
        "note": (
            f"Correlation computed on {level.replace('-', ' ')} aggregates. "
            "Row-level correlation is also available for reference — it's often "
            "misleading when unit price varies between products."
        ),
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
def anomalies(df: pd.DataFrame, measure: str, date_col: Optional[str] = None,
               context_columns: Optional[list[str]] = None) -> dict:
    """
    Flag rows where `measure` sits more than 3σ from the mean.
    Each row includes the date, all provided context columns (Product /
    Country / Salesperson etc.), the value, the z-score, and a human-readable
    reason so the user can investigate.
    """
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

    # Which columns to attach for investigation
    ctx = list(context_columns or [])
    if not ctx:
        # Autopick: first 3 non-numeric non-date columns
        for c in df.columns:
            if c in ctx or c == measure or c == date_col:
                continue
            if not pd.api.types.is_numeric_dtype(df[c]) and not pd.api.types.is_datetime64_any_dtype(df[c]):
                ctx.append(c)
            if len(ctx) >= 3:
                break

    out: list[dict] = []
    for idx, val in flagged.items():
        z_val = round(float(z.loc[idx]), 2)
        direction = "above" if z_val > 0 else "below"
        reason = (
            f"{safe_val(val)} is {abs(z_val)}σ {direction} the mean "
            f"({round(mean, 2)})"
        )
        # +2 to match Excel: pandas is 0-indexed and Excel row 1 is the header,
        # so DataFrame idx 0 == Excel row 2.
        item = {
            "index": int(idx),                  # raw pandas index (used as React key)
            "row_number": int(idx) + 2,          # what the user sees in Excel
            "value": safe_val(val),
            "z": z_val,
            "reason": reason,
        }
        if date_col and date_col in df.columns:
            item["date"] = safe_val(df.loc[idx, date_col])
        for c in ctx:
            if c in df.columns:
                try:
                    item[c] = safe_val(df.loc[idx, c])
                except Exception:
                    pass
        out.append(item)

    # Sort by |z| descending — biggest anomalies first
    out.sort(key=lambda r: abs(r["z"]), reverse=True)
    return {
        "measure": measure,
        "mean": round(mean, 2),
        "std": round(std, 2),
        "threshold_z": 3.0,
        "context_columns": ctx,
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
    grouped["contribution_pct"] = grouped[measure] / total * 100
    grouped["cum_pct"] = grouped["contribution_pct"].cumsum()
    grouped["rank"] = range(1, len(grouped) + 1)
    grouped["abc"] = pd.cut(
        grouped["cum_pct"], bins=[-0.01, 70, 90, 100.01], labels=["A", "B", "C"]
    ).astype(str)
    top_80 = int((grouped["cum_pct"] <= 80).sum())
    total_items = int(len(grouped))
    return {
        "dimension": dimension,
        "measure": measure,
        "total": round(total, 2),
        "total_items": total_items,
        "top_80pct_count": top_80,
        "top_80pct_share_of_items": round(top_80 / total_items * 100, 1) if total_items else 0,
        "class_counts": {
            "A": int((grouped["abc"] == "A").sum()),
            "B": int((grouped["abc"] == "B").sum()),
            "C": int((grouped["abc"] == "C").sum()),
        },
        "items": records(grouped.head(50)),
    }


# ---------------------------------------------------------------------------
# Root-cause / contribution-to-change analysis
# ---------------------------------------------------------------------------
def root_cause(df: pd.DataFrame, measure: str, dimension: str,
                date_col: Optional[str] = None, freq: str = "MS") -> dict:
    """
    Explain what drove the recent change in `measure`.

    Compares the last two complete `freq` periods, computes each `dimension`
    value's contribution to the delta, and returns the top gainers, top
    decliners, and a summary sentence. This is what "why did X change"
    actually needs.
    """
    if measure not in df.columns or dimension not in df.columns:
        return {"gainers": [], "decliners": [], "note": "missing columns"}

    tmp = df[[dimension, measure]].copy()
    tmp[measure] = pd.to_numeric(tmp[measure], errors="coerce")

    if date_col and date_col in df.columns:
        tmp[date_col] = pd.to_datetime(df[date_col], errors="coerce")
        tmp = tmp.dropna(subset=[date_col, measure])
        if not len(tmp):
            return {"gainers": [], "decliners": [], "note": "no valid data"}

        # Drop partial last month
        buckets = tmp.set_index(date_col).groupby([pd.Grouper(freq=freq), dimension])[measure].sum()
        wide = buckets.unstack(fill_value=0)
        max_date = tmp[date_col].max()
        if len(wide.index):
            last_end = (wide.index[-1] + pd.offsets.MonthEnd(0)).normalize()
            if pd.notna(max_date) and max_date.normalize() < last_end:
                wide = wide.iloc[:-1]

        if len(wide) < 2:
            return {"gainers": [], "decliners": [], "note": "need at least 2 complete periods"}

        curr, prev = wide.iloc[-1], wide.iloc[-2]
        delta = (curr - prev).sort_values(ascending=False)
        period_curr = str(wide.index[-1].date())
        period_prev = str(wide.index[-2].date())
    else:
        # No time — just show largest positive/negative dim values
        tmp = tmp.dropna(subset=[measure])
        total = tmp.groupby(dimension)[measure].sum().sort_values(ascending=False)
        return {
            "gainers": records(total.head(5).reset_index()),
            "decliners": records(total.tail(5).reset_index()),
            "period_curr": None, "period_prev": None,
            "note": "no date column — showing top and bottom contributors overall",
        }

    total_delta = float(curr.sum() - prev.sum())
    prev_total = float(prev.sum())
    pct_change = (total_delta / abs(prev_total) * 100) if prev_total else None

    gainers = delta.head(5).reset_index()
    gainers.columns = [dimension, "delta"]
    gainers[measure + "_prev"] = [float(prev.get(x, 0)) for x in gainers[dimension]]
    gainers[measure + "_curr"] = [float(curr.get(x, 0)) for x in gainers[dimension]]

    decliners = delta.tail(5).reset_index()
    decliners.columns = [dimension, "delta"]
    decliners[measure + "_prev"] = [float(prev.get(x, 0)) for x in decliners[dimension]]
    decliners[measure + "_curr"] = [float(curr.get(x, 0)) for x in decliners[dimension]]

    return {
        "measure": measure,
        "dimension": dimension,
        "period_prev": period_prev,
        "period_curr": period_curr,
        "prev_total": round(prev_total, 2),
        "curr_total": round(float(curr.sum()), 2),
        "delta_total": round(total_delta, 2),
        "pct_change": round(pct_change, 2) if pct_change is not None else None,
        "gainers": records(gainers),
        "decliners": records(decliners.iloc[::-1]),
    }


# ---------------------------------------------------------------------------
# Count / distribution — for datasets without numeric measures
# ---------------------------------------------------------------------------
def count_by(df: pd.DataFrame, dimension: str, n: int = 20,
              ascending: bool = False) -> dict:
    """
    Group by `dimension` and count rows. Used for HR-style data where the
    question is "how many X" rather than "sum X". Returns records with
    columns `<dimension>`, `count`, `percentage`.
    """
    if dimension not in df.columns:
        return {"items": [], "total": 0, "dimension": dimension,
                "note": "missing column"}
    grouped = (
        df[dimension].fillna("<null>").astype(str)
        .value_counts(dropna=False)
        .head(n)
        .rename_axis(dimension)
        .reset_index(name="count")
    )
    total = int(df[dimension].notna().sum() + (df[dimension].isna().sum() if df[dimension].isna().any() else 0))
    grouped["percentage"] = (grouped["count"] / max(total, 1) * 100).round(2)
    if ascending:
        grouped = grouped.iloc[::-1].reset_index(drop=True)
    return {
        "dimension": dimension,
        "items": records(grouped),
        "total": total,
        "unique_values": int(df[dimension].nunique(dropna=True)),
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

    # ------------------------------------------------------------------
    # Interpretation: skew, kurtosis, modality, outliers
    # ------------------------------------------------------------------
    interpretation: dict = {"observations": []}
    try:
        skew = float(s.skew())
        kurt = float(s.kurt())
        interpretation["skew"] = round(skew, 2)
        interpretation["kurtosis"] = round(kurt, 2)
        if skew > 0.5:
            shape = "right-skewed — a long tail of large values"
        elif skew < -0.5:
            shape = "left-skewed — a long tail of small values"
        else:
            shape = "roughly symmetric"
        interpretation["shape"] = shape
        interpretation["observations"].append(
            f"Distribution is {shape} (skew {skew:+.2f})."
        )
        if kurt > 3:
            interpretation["observations"].append(
                f"Heavy tails / peaked (excess kurtosis {kurt:+.2f}) — expect more extreme values than a normal distribution."
            )
        elif kurt < -1:
            interpretation["observations"].append(
                f"Flat / uniform-ish shape (excess kurtosis {kurt:+.2f})."
            )
    except Exception:
        pass

    # Modality via local peaks in the histogram (crude but useful)
    try:
        peak_positions = []
        for i in range(1, len(counts) - 1):
            if counts[i] > counts[i - 1] and counts[i] > counts[i + 1] and counts[i] >= max(counts) * 0.2:
                peak_positions.append(i)
        n_modes = len(peak_positions) if peak_positions else (1 if len(counts) else 0)
        interpretation["mode_count"] = int(n_modes)
        if n_modes >= 2:
            interpretation["observations"].append(
                f"Multimodal — {n_modes} clear peaks. The population may be a mix of sub-groups worth segmenting."
            )
        else:
            interpretation["observations"].append("Unimodal — a single dominant peak.")
    except Exception:
        pass

    # Outliers via IQR
    try:
        q1, q3 = float(s.quantile(0.25)), float(s.quantile(0.75))
        iqr = q3 - q1
        lower, upper = q1 - 1.5 * iqr, q3 + 1.5 * iqr
        outlier_count = int(((s < lower) | (s > upper)).sum())
        interpretation["outlier_count"] = outlier_count
        interpretation["outlier_range"] = [round(lower, 2), round(upper, 2)]
        if outlier_count > 0:
            share = outlier_count / len(s) * 100
            interpretation["observations"].append(
                f"{outlier_count} outliers ({share:.1f}% of rows) sit outside the "
                f"Tukey fence [{lower:,.0f}, {upper:,.0f}]."
            )
        else:
            interpretation["observations"].append("No IQR outliers detected.")
    except Exception:
        pass

    return {
        "measure": measure,
        "interpretation": interpretation,
        "bins": [
            {"range": [safe_val(edges[i]), safe_val(edges[i + 1])], "count": int(counts[i])}
            for i in range(len(counts))
        ],
    }
