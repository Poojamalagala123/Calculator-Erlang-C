"""STL decomposition forecasting integrated with Erlang C staffing."""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

import numpy as np
import pandas as pd
from statsmodels.tsa.seasonal import STL

from calculator import (
    FORECAST_COLUMNS,
    _cached_required_agents,
    build_interval_forecast,
    clean_percent,
    infer_single_year,
    preprocess_cdr,
    read_cdr_csv,
)


def _validate_interval(interval_minutes: int) -> int:
    interval_minutes = int(interval_minutes)
    if interval_minutes <= 0 or 1440 % interval_minutes != 0:
        raise ValueError("interval_minutes must be a positive divisor of 1440.")
    return interval_minutes


def _prepare_training_series(
    file_paths: Sequence[str | Path],
    filenames: Sequence[str] | None,
    interval_minutes: int,
) -> tuple[pd.DataFrame, dict]:
    if len(file_paths) < 2:
        raise ValueError("Upload at least two full-year CDR datasets.")
    if filenames is not None and len(filenames) != len(file_paths):
        raise ValueError("filenames and file_paths must have the same length.")

    yearly_intervals: list[pd.DataFrame] = []
    dataset_summaries: list[dict] = []
    years: list[int] = []
    seen_years: set[int] = set()

    for index, file_path in enumerate(file_paths):
        filename = filenames[index] if filenames else Path(file_path).name
        raw_rows = len(read_cdr_csv(file_path))
        clean = preprocess_cdr(file_path)
        if clean.empty:
            raise ValueError(f"No valid Answered records found in {filename}.")

        year = infer_single_year(clean, filename)
        if year in seen_years:
            raise ValueError(f"Duplicate year {year}. Upload one dataset per year.")
        seen_years.add(year)
        years.append(year)

        intervals = build_interval_forecast(
            clean,
            interval_minutes=interval_minutes,
            include_empty_intervals=True,
            year=year,
        )
        intervals["source_year"] = year
        yearly_intervals.append(intervals)

        valid_calls = int(len(clean))
        total_handle_time = float(clean["duration_seconds"].sum())
        dataset_summaries.append(
            {
                "filename": filename,
                "year": year,
                "raw_rows": int(raw_rows),
                "valid_answered_calls": valid_calls,
                "removed_rows": int(raw_rows - valid_calls),
                "average_aht_seconds": round(total_handle_time / valid_calls, 2),
                "first_call": clean["call_datetime"].min().isoformat(),
                "last_call": clean["call_datetime"].max().isoformat(),
            }
        )

    years = sorted(years)
    expected_years = list(range(years[0], years[-1] + 1))
    if years != expected_years:
        raise ValueError(
            "STL forecasting requires consecutive historical years; "
            f"received {years}."
        )

    history = pd.concat(yearly_intervals, ignore_index=True).sort_values(
        "call_datetime"
    )
    history = history.reset_index(drop=True)
    history["weekday"] = history["call_datetime"].dt.dayofweek
    history["hour"] = history["call_datetime"].dt.hour
    history["minute"] = history["call_datetime"].dt.minute

    total_calls = int(history["call_volume"].sum())
    total_handle_time = float(history["total_handle_time_seconds"].sum())
    if total_calls <= 0:
        raise ValueError("The historical datasets contain no valid answered calls.")

    return history, {
        "dataset_count": len(file_paths),
        "historical_years": years,
        "datasets": sorted(dataset_summaries, key=lambda item: item["year"]),
        "total_valid_answered_calls": total_calls,
        "global_weighted_aht_seconds": round(total_handle_time / total_calls, 2),
    }


def _forecast_damped_trend(
    trend: np.ndarray,
    forecast_length: int,
    seasonal_period: int,
    damping: float = 0.98,
) -> np.ndarray:
    window_length = min(len(trend), max(seasonal_period * 8, seasonal_period + 1))
    recent_trend = np.asarray(trend[-window_length:], dtype=float)
    x_values = np.arange(window_length, dtype=float)
    slope, _ = np.polyfit(x_values, recent_trend, 1)
    steps = np.arange(1, forecast_length + 1, dtype=float)
    damped_steps = (1 - np.power(damping, steps)) / (1 - damping)
    return float(recent_trend[-1]) + slope * damped_steps


def _build_decomposition_summary(
    timestamps: pd.Series,
    observed: np.ndarray,
    result,
) -> list[dict]:
    components = pd.DataFrame(
        {
            "interval_start": pd.to_datetime(timestamps),
            "observed_calls": observed,
            "trend": np.asarray(result.trend, dtype=float),
            "seasonal": np.asarray(result.seasonal, dtype=float),
            "irregular": np.asarray(result.resid, dtype=float),
        }
    ).set_index("interval_start")
    daily = components.resample("1D").agg(
        observed_calls=("observed_calls", "sum"),
        trend=("trend", "sum"),
        seasonal=("seasonal", "sum"),
        irregular=("irregular", "sum"),
    )
    daily = daily.reset_index()
    daily["interval_start"] = daily["interval_start"].dt.strftime("%Y-%m-%d")
    return daily.round(4).to_dict(orient="records")


def build_stl_forecast(
    file_paths: Sequence[str | Path],
    filenames: Sequence[str] | None = None,
    interval_minutes: int = 30,
    seasonal_period: int | None = None,
    target_seconds: float = 20,
    target_service_level: float = 80,
    shrinkage: float = 30,
    max_agents: int = 1000,
) -> tuple[pd.DataFrame, dict]:
    """Forecast the next calendar year with STL and calculate Erlang C staffing."""
    interval_minutes = _validate_interval(interval_minutes)
    intervals_per_day = 1440 // interval_minutes
    resolved_period = int(seasonal_period or intervals_per_day * 7)
    if resolved_period < 2:
        raise ValueError("seasonal_period must be at least 2.")

    history, source_summary = _prepare_training_series(
        file_paths,
        filenames,
        interval_minutes,
    )
    observed = history["call_volume"].astype(float).to_numpy()
    if len(observed) < resolved_period * 2:
        raise ValueError(
            "STL requires at least two complete seasonal cycles in the training data."
        )

    smoothing_jump = max(1, resolved_period // 20)
    decomposition = STL(
        observed,
        period=resolved_period,
        robust=True,
        seasonal_jump=smoothing_jump,
        trend_jump=smoothing_jump,
        low_pass_jump=smoothing_jump,
    ).fit()

    output_year = max(source_summary["historical_years"]) + 1
    rule = f"{interval_minutes}min"
    forecast_dates = pd.date_range(
        start=f"{output_year}-01-01 00:00:00",
        end=f"{output_year + 1}-01-01 00:00:00",
        freq=rule,
        inclusive="left",
    )
    # Keep output compatible with the existing fixed 365-day dashboard.
    forecast_dates = forecast_dates[
        ~((forecast_dates.month == 2) & (forecast_dates.day == 29))
    ]
    forecast_length = len(forecast_dates)

    seasonal_cycle = np.asarray(decomposition.seasonal[-resolved_period:], dtype=float)
    seasonal_forecast = np.resize(seasonal_cycle, forecast_length)
    trend_forecast = _forecast_damped_trend(
        np.asarray(decomposition.trend, dtype=float),
        forecast_length,
        resolved_period,
    )
    predicted_calls = np.maximum(0, trend_forecast + seasonal_forecast)

    aht_pattern = (
        history.groupby(["weekday", "hour", "minute"], as_index=False)
        .agg(
            total_handle_time_seconds=("total_handle_time_seconds", "sum"),
            answered_calls=("call_volume", "sum"),
        )
    )
    global_aht = float(source_summary["global_weighted_aht_seconds"])
    aht_pattern["predicted_aht_seconds"] = (
        aht_pattern["total_handle_time_seconds"]
        .div(aht_pattern["answered_calls"].where(aht_pattern["answered_calls"].ne(0)))
        .fillna(global_aht)
        .astype(float)
    )

    forecast = pd.DataFrame(
        {
            "interval_start": forecast_dates,
            "predicted_call_volume_unrounded": predicted_calls,
            "trend_component": trend_forecast,
            "seasonal_component": seasonal_forecast,
        }
    )
    forecast["weekday"] = forecast["interval_start"].dt.dayofweek
    forecast["hour"] = forecast["interval_start"].dt.hour
    forecast["minute"] = forecast["interval_start"].dt.minute
    forecast = forecast.merge(
        aht_pattern[
            ["weekday", "hour", "minute", "predicted_aht_seconds"]
        ],
        on=["weekday", "hour", "minute"],
        how="left",
        validate="many_to_one",
    )
    forecast["predicted_aht_seconds"] = forecast[
        "predicted_aht_seconds"
    ].fillna(global_aht)
    forecast["call_volume"] = (
        forecast["predicted_call_volume_unrounded"].round().clip(lower=0).astype(int)
    )

    interval_seconds = interval_minutes * 60
    staffing_rows = [
        _cached_required_agents(
            int(row.call_volume),
            round(float(row.predicted_aht_seconds), 2),
            interval_seconds,
            float(target_seconds),
            float(target_service_level),
            float(shrinkage),
            int(max_agents),
        )
        for row in forecast.itertuples(index=False)
    ]
    staffing = pd.DataFrame(
        staffing_rows,
        columns=[
            "traffic_erlangs",
            "raw_agents",
            "scheduled_agents",
            "service_level",
            "probability_waiting",
            "occupancy",
            "asa_seconds",
        ],
    )
    forecast = pd.concat([forecast.reset_index(drop=True), staffing], axis=1)
    forecast["date"] = forecast["interval_start"].dt.strftime("%Y-%m-%d")
    forecast["day_of_year"] = (
        forecast["interval_start"].dt.normalize().factorize()[0] + 1
    )
    forecast["month"] = forecast["interval_start"].dt.month
    forecast["day"] = forecast["interval_start"].dt.day
    forecast["weekday_name"] = forecast["interval_start"].dt.day_name()
    forecast["aht_seconds"] = forecast.pop("predicted_aht_seconds")
    forecast["service_level_percent"] = forecast.pop("service_level") * 100
    forecast["probability_waiting_percent"] = (
        forecast.pop("probability_waiting") * 100
    )
    forecast["occupancy_percent"] = forecast.pop("occupancy") * 100
    forecast = forecast.round(
        {
            "predicted_call_volume_unrounded": 4,
            "trend_component": 4,
            "seasonal_component": 4,
            "aht_seconds": 2,
            "traffic_erlangs": 4,
            "service_level_percent": 2,
            "probability_waiting_percent": 2,
            "occupancy_percent": 2,
            "asa_seconds": 2,
        }
    )

    total_calls = int(forecast["call_volume"].sum())
    peak_row = forecast.loc[forecast["call_volume"].idxmax()]
    target_percent = clean_percent(target_service_level) * 100
    summary = {
        "forecast_method": "stl",
        "logic": (
            "Seasonal-Trend Decomposition using LOESS with a damped trend "
            "and a repeated seasonal cycle."
        ),
        "dataset_count": source_summary["dataset_count"],
        "historical_years": source_summary["historical_years"],
        "output_year": output_year,
        "days": 365,
        "interval_minutes": interval_minutes,
        "seasonal_period": resolved_period,
        "forecast_interval_count": int(len(forecast)),
        "total_predicted_calls": total_calls,
        "average_aht_seconds": round(
            float((forecast["aht_seconds"] * forecast["call_volume"]).sum())
            / max(total_calls, 1),
            2,
        ),
        "maximum_raw_agents": int(forecast["raw_agents"].max()),
        "maximum_scheduled_agents": int(forecast["scheduled_agents"].max()),
        "average_service_level_percent": round(
            float(forecast["service_level_percent"].mean()), 2
        ),
        "average_occupancy_percent": round(
            float(forecast["occupancy_percent"].mean()), 2
        ),
        "average_asa_seconds": round(float(forecast["asa_seconds"].mean()), 2),
        "intervals_below_service_target": int(
            (forecast["service_level_percent"] + 1e-9 < target_percent).sum()
        ),
        "peak_interval": {
            "interval_start": peak_row["interval_start"].isoformat(),
            "call_volume": int(peak_row["call_volume"]),
            "scheduled_agents": int(peak_row["scheduled_agents"]),
        },
        "source_data": source_summary,
        "decomposition": _build_decomposition_summary(
            history["call_datetime"],
            observed,
            decomposition,
        ),
    }
    ordered_columns = FORECAST_COLUMNS + [
        "predicted_call_volume_unrounded",
        "trend_component",
        "seasonal_component",
    ]
    return forecast[ordered_columns], summary


