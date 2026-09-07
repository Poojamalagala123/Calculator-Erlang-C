from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional, Sequence
import numpy as np
import pandas as pd
from statsmodels.tsa.seasonal import STL

from app.core.constants import FORECAST_COLUMNS
from app.core.ingestion.reader import read_cdr_csv
from app.core.ingestion.cleaner import preprocess_cdr, infer_single_year, clean_percent
from app.core.ingestion.interval import build_cdr_intervals
from app.core.queuing.staffing import compute_interval_staffing

def build_stl_forecast(
    file_paths: Sequence[str | Path],
    filenames: Sequence[str] | None = None,
    interval_minutes: int = 30,
    forecast_days: int = 365,
    seasonal_period: int | None = None,
    trend_lookback_days: int = 90,
    target_seconds: float = 20,
    target_service_level: float = 80,
    shrinkage: float = 30,
    max_agents: int = 1000,
    progress_callback: Optional[Callable[[int, str], None]] = None,
) -> tuple[pd.DataFrame, dict]:
    if not file_paths:
        raise ValueError("Upload at least one full-year CDR dataset.")
    if filenames is not None and len(filenames) != len(file_paths):
        raise ValueError("filenames and file_paths must have the same length.")

    interval_minutes = int(interval_minutes)
    forecast_days = int(forecast_days)
    trend_lookback_days = int(trend_lookback_days)
    if interval_minutes <= 0 or 1440 % interval_minutes != 0:
        raise ValueError("interval_minutes must be a positive divisor of 1440.")
    if forecast_days <= 0:
        raise ValueError("forecast_days must be greater than 0.")
    if trend_lookback_days < 7:
        raise ValueError("trend_lookback_days must be at least 7.")

    intervals_per_day = 1440 // interval_minutes
    resolved_period = int(seasonal_period or intervals_per_day * 7)
    if resolved_period < 2:
        raise ValueError("seasonal_period must be at least 2 intervals.")

    dataset_frames: list[pd.DataFrame] = []
    dataset_summary: list[dict] = []
    seen_years: set[int] = set()

    total_files = len(file_paths)
    for index, path in enumerate(file_paths):
        filename = filenames[index] if filenames else Path(path).name
        if progress_callback:
            percent = 10 + int(30 * (index / total_files))
            progress_callback(percent, f"Reading and cleaning {filename} ({index + 1}/{total_files})...")

        # Fast single-pass read & clean (avoids reading file twice)
        raw_frame = read_cdr_csv(path)
        raw_count = len(raw_frame)
        clean = preprocess_cdr(path, raw_frame=raw_frame)

        if clean.empty:
            raise ValueError(f"No valid Answered records found in {filename}.")
        year = infer_single_year(clean, filename)
        if year in seen_years:
            raise ValueError(f"Duplicate year {year}. Upload only one dataset for each year.")
        seen_years.add(year)

        frame = build_cdr_intervals(
            clean,
            interval_minutes=interval_minutes,
            include_empty_intervals=True,
            year=year,
        ).rename(columns={"call_datetime": "interval_start"})
        frame = frame.loc[
            ~((frame["interval_start"].dt.month == 2) & (frame["interval_start"].dt.day == 29))
        ].copy()
        frame["source_year"] = year
        dataset_frames.append(frame)

        answered_calls = int(len(clean))
        total_handle = float(clean["duration_seconds"].sum())
        dataset_summary.append({
            "filename": filename,
            "year": year,
            "raw_rows": int(raw_count),
            "valid_answered_calls": answered_calls,
            "removed_rows": int(raw_count - answered_calls),
            "average_aht_seconds": round(total_handle / answered_calls, 2),
            "first_call": clean["call_datetime"].min().isoformat(),
            "last_call": clean["call_datetime"].max().isoformat(),
        })

    if progress_callback:
        progress_callback(45, "Combining historical intervals and preparing time series...")

    historical = pd.concat(dataset_frames, ignore_index=True).sort_values("interval_start")
    historical = historical.reset_index(drop=True)
    if len(historical) < resolved_period * 2:
        raise ValueError(
            f"STL needs at least two complete seasonal cycles ({resolved_period * 2} intervals)."
        )

    if progress_callback:
        progress_callback(55, "Running STL seasonal-trend decomposition...")

    call_series = historical["call_volume"].astype(float)
    seasonal_len = 7
    trend_len = int(np.ceil(1.5 * resolved_period / (1 - 1.5 / seasonal_len)))
    if trend_len % 2 == 0:
        trend_len += 1
    trend_jump = max(1, int(np.ceil(trend_len / 10)))
    low_pass_jump = max(1, int(np.ceil(resolved_period / 10)))

    decomposition = STL(
        call_series,
        period=resolved_period,
        seasonal=seasonal_len,
        seasonal_jump=1,
        trend_jump=trend_jump,
        low_pass_jump=low_pass_jump,
        robust=True,
    ).fit()

    horizon = forecast_days * intervals_per_day
    lookback = min(len(decomposition.trend), trend_lookback_days * intervals_per_day)
    trend_tail = np.asarray(decomposition.trend.iloc[-lookback:], dtype=float)
    x = np.arange(lookback, dtype=float)
    slope, intercept = np.polyfit(x, trend_tail, 1)
    future_x = np.arange(lookback, lookback + horizon, dtype=float)
    future_trend = intercept + slope * future_x

    seasonal_cycle = np.asarray(decomposition.seasonal.iloc[-resolved_period:], dtype=float)
    future_seasonal = np.resize(seasonal_cycle, horizon)
    predicted_calls = np.clip(np.rint(future_trend + future_seasonal), 0, None).astype(int)

    if progress_callback:
        progress_callback(65, "Estimating slot-level Average Handling Times (AHT)...")

    historical["slot"] = (
        historical["interval_start"].dt.dayofweek * intervals_per_day
        + (historical["interval_start"].dt.hour * 60 + historical["interval_start"].dt.minute) // interval_minutes
    )
    global_aht = (
        historical["total_handle_time_seconds"].sum()
        / historical["call_volume"].sum()
        if historical["call_volume"].sum() > 0 else 1.0
    )
    slot_aht = (
        historical.groupby("slot")
        .agg(total_handle=("total_handle_time_seconds", "sum"), calls=("call_volume", "sum"))
    )
    slot_aht["aht_seconds"] = (
        slot_aht["total_handle"] / slot_aht["calls"].replace(0, np.nan)
    ).fillna(global_aht)

    output_year = max(seen_years) + 1
    start = pd.Timestamp(f"{output_year}-01-01 00:00:00")
    future_index = pd.date_range(start=start, periods=horizon, freq=f"{interval_minutes}min")
    future = pd.DataFrame({"interval_start": future_index})
    future = future.loc[
        ~((future["interval_start"].dt.month == 2) & (future["interval_start"].dt.day == 29))
    ].head(horizon).reset_index(drop=True)
    if len(future) < horizon:
        extra = pd.date_range(
            start=future_index[-1] + pd.Timedelta(minutes=interval_minutes),
            periods=horizon - len(future) + intervals_per_day,
            freq=f"{interval_minutes}min",
        )
        extra = extra[~((extra.month == 2) & (extra.day == 29))]
        future = pd.concat([future, pd.DataFrame({"interval_start": extra})], ignore_index=True).head(horizon)

    future["call_volume"] = predicted_calls[:len(future)]
    future["slot"] = (
        future["interval_start"].dt.dayofweek * intervals_per_day
        + (future["interval_start"].dt.hour * 60 + future["interval_start"].dt.minute) // interval_minutes
    )
    future["aht_seconds"] = future["slot"].map(slot_aht["aht_seconds"]).fillna(global_aht).astype(float)

    if progress_callback:
        progress_callback(75, f"Calculating Erlang C staffing across {len(future):,} intervals...")

    interval_seconds = interval_minutes * 60
    metrics = compute_interval_staffing(
        future_df=future,
        interval_seconds=interval_seconds,
        target_seconds=float(target_seconds),
        target_service_level=float(target_service_level),
        shrinkage=float(shrinkage),
        max_agents=int(max_agents),
    )

    forecast = pd.concat([future.drop(columns=["slot"]).reset_index(drop=True), metrics], axis=1)
    forecast["date"] = forecast["interval_start"].dt.strftime("%Y-%m-%d")
    forecast["day_of_year"] = forecast["interval_start"].dt.dayofyear
    forecast["month"] = forecast["interval_start"].dt.month
    forecast["day"] = forecast["interval_start"].dt.day
    forecast["weekday"] = forecast["interval_start"].dt.dayofweek
    forecast["weekday_name"] = forecast["interval_start"].dt.day_name()
    forecast["hour"] = forecast["interval_start"].dt.hour
    forecast["minute"] = forecast["interval_start"].dt.minute
    forecast["service_level_percent"] = forecast.pop("service_level") * 100
    forecast["probability_waiting_percent"] = forecast.pop("probability_waiting") * 100
    forecast["occupancy_percent"] = forecast.pop("occupancy") * 100
    forecast = forecast.round({
        "aht_seconds": 2, "traffic_erlangs": 4, "service_level_percent": 2,
        "probability_waiting_percent": 2, "occupancy_percent": 2, "asa_seconds": 2,
    })[FORECAST_COLUMNS]

    if progress_callback:
        progress_callback(92, "Generating summary statistics and peak analysis...")

    peak_row = forecast.loc[forecast["call_volume"].idxmax()]
    target_percent = clean_percent(target_service_level) * 100
    summary = {
        "method": "STL",
        "logic": "STL weekly decomposition with linear trend extrapolation and repeated seasonal cycle.",
        "dataset_count": len(dataset_frames),
        "historical_years": sorted(seen_years),
        "output_year": int(output_year),
        "days": int(forecast_days),
        "interval_minutes": interval_minutes,
        "seasonal_period": resolved_period,
        "trend_lookback_days": trend_lookback_days,
        "forecast_interval_count": int(len(forecast)),
        "total_predicted_calls": int(forecast["call_volume"].sum()),
        "average_aht_seconds": round(float(np.average(
            forecast["aht_seconds"], weights=forecast["call_volume"].clip(lower=1)
        )), 2),
        "maximum_raw_agents": int(forecast["raw_agents"].max()),
        "maximum_scheduled_agents": int(forecast["scheduled_agents"].max()),
        "average_service_level_percent": round(float(forecast["service_level_percent"].mean()), 2),
        "average_occupancy_percent": round(float(forecast["occupancy_percent"].mean()), 2),
        "average_asa_seconds": round(float(forecast["asa_seconds"].mean()), 2),
        "intervals_below_service_target": int((forecast["service_level_percent"] < target_percent).sum()),
        "peak_interval": {
            "interval_start": peak_row["interval_start"].isoformat(),
            "call_volume": int(peak_row["call_volume"]),
            "scheduled_agents": int(peak_row["scheduled_agents"]),
        },
        "source_data": {
            "dataset_count": len(dataset_frames),
            "historical_years": sorted(seen_years),
            "datasets": sorted(dataset_summary, key=lambda item: item["year"]),
        },
        "decomposition_summary": {
            "historical_intervals": int(len(historical)),
            "trend_last_value": round(float(decomposition.trend.iloc[-1]), 4),
            "trend_slope_per_interval": round(float(slope), 8),
            "seasonal_min": round(float(decomposition.seasonal.min()), 4),
            "seasonal_max": round(float(decomposition.seasonal.max()), 4),
            "residual_std": round(float(decomposition.resid.std()), 4),
        },
    }
    return forecast, summary
