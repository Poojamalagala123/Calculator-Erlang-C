from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Optional, Sequence

import numpy as np
import pandas as pd
from pyworkforce.queuing import ErlangC
from statsmodels.tsa.seasonal import STL

CDR_COLUMNS = [
    "source",
    "destination",
    "call_datetime",
    "duration",
    "disposition",
    "unique_id",
    "caller_id",
]

FORECAST_COLUMNS = [
    "interval_start",
    "date",
    "day_of_year",
    "month",
    "day",
    "weekday",
    "weekday_name",
    "hour",
    "minute",
    "call_volume",
    "aht_seconds",
    "traffic_erlangs",
    "raw_agents",
    "scheduled_agents",
    "service_level_percent",
    "probability_waiting_percent",
    "occupancy_percent",
    "asa_seconds",
]


def hms_to_seconds(value) -> Optional[int]:
    if pd.isna(value):
        return None
    parts = str(value).strip().split(":")
    if len(parts) != 3:
        return None
    try:
        hours, minutes, seconds = (int(part) for part in parts)
    except (TypeError, ValueError):
        return None
    if hours < 0 or not 0 <= minutes < 60 or not 0 <= seconds < 60:
        return None
    return hours * 3600 + minutes * 60 + seconds


def clean_percent(value) -> float:
    number = float(str(value).strip().replace("%", ""))
    if number > 1:
        number /= 100
    return number


def calculate_aht(total_handle_time_seconds: float, total_answered_calls: int) -> float:
    if total_handle_time_seconds < 0:
        raise ValueError("Total handle time cannot be negative.")
    if total_answered_calls <= 0:
        raise ValueError("Answered calls must be greater than 0.")
    return total_handle_time_seconds / total_answered_calls


def calculate_traffic(call_volume: float, aht_seconds: float, interval_seconds: float) -> float:
    if call_volume < 0:
        raise ValueError("Call volume cannot be negative.")
    if aht_seconds <= 0:
        raise ValueError("AHT seconds must be greater than 0.")
    if interval_seconds <= 0:
        raise ValueError("Interval seconds must be greater than 0.")
    return (call_volume * aht_seconds) / interval_seconds


def _erlang_model_from_traffic(traffic: float, aht_seconds: float, target_seconds: float) -> ErlangC:
    if traffic < 0:
        raise ValueError("Traffic cannot be negative.")
    if aht_seconds <= 0:
        raise ValueError("AHT seconds must be greater than 0.")
    if target_seconds < 0:
        raise ValueError("Target seconds cannot be negative.")

    interval_minutes = 60
    aht_minutes = aht_seconds / 60
    transactions = traffic * interval_minutes / aht_minutes
    return ErlangC(
        transactions=transactions,
        aht=aht_minutes,
        asa=target_seconds / 60,
        interval=interval_minutes,
    )


def erlang_c_probability(traffic: float, agents: int) -> float:
    agents = int(agents)
    if agents <= 0:
        raise ValueError("Agents must be greater than 0.")
    if agents <= traffic:
        return 1.0
    return _erlang_model_from_traffic(traffic, 60, 0).waiting_probability(agents)


def average_speed_of_answer(erlang_c: float, aht_seconds: float, agents: int, traffic: float) -> float:
    agents = int(agents)
    if agents <= 0:
        raise ValueError("Agents must be greater than 0.")
    if agents <= traffic:
        return float("inf")
    return erlang_c * aht_seconds / (agents - traffic)


def service_level(
    erlang_c: float,
    agents: int,
    traffic: float,
    target_seconds: float,
    aht_seconds: float,
) -> float:
    agents = int(agents)
    if agents <= 0:
        raise ValueError("Agents must be greater than 0.")
    if agents <= traffic:
        return 0.0
    return _erlang_model_from_traffic(traffic, aht_seconds, target_seconds).service_level(agents)


def occupancy(traffic: float, agents: int) -> float:
    agents = int(agents)
    if agents <= 0:
        raise ValueError("Agents must be greater than 0.")
    if agents <= traffic:
        return traffic / agents
    return _erlang_model_from_traffic(traffic, 60, 0).achieved_occupancy(agents)


def required_agents(
    call_volume: float,
    aht_seconds: float,
    interval_seconds: float,
    target_seconds: float,
    target_service_level: float,
    shrinkage: float = 0.0,
    max_agents: int = 1000,
) -> dict:
    target_service_level = clean_percent(target_service_level)
    shrinkage = clean_percent(shrinkage)

    if call_volume < 0:
        raise ValueError("Call volume cannot be negative.")
    if aht_seconds <= 0:
        raise ValueError("AHT seconds must be greater than 0.")
    if interval_seconds <= 0:
        raise ValueError("Interval seconds must be greater than 0.")
    if target_seconds < 0:
        raise ValueError("Target seconds cannot be negative.")
    if not 0 < target_service_level < 1:
        raise ValueError("Target service level must be between 0% and 100%.")
    if not 0 <= shrinkage < 1:
        raise ValueError("Shrinkage must be between 0% and less than 100%.")
    if max_agents <= 0:
        raise ValueError("max_agents must be greater than 0.")

    if call_volume == 0:
        return {
            "traffic_erlangs": 0.0,
            "raw_agents": 0,
            "scheduled_agents": 0,
            "service_level": 1.0,
            "probability_waiting": 0.0,
            "occupancy": 0.0,
            "asa_seconds": 0.0,
        }

    model = ErlangC(
        transactions=call_volume,
        aht=aht_seconds / 60,
        asa=target_seconds / 60,
        interval=interval_seconds / 60,
        shrinkage=shrinkage,
    )
    result = model.required_positions(service_level=target_service_level)

    raw_agents = int(result["raw_positions"])
    scheduled_agents = int(result["positions"])
    if raw_agents > max_agents or scheduled_agents > max_agents:
        raise RuntimeError("Required agents exceed max_agents limit.")

    traffic = calculate_traffic(call_volume, aht_seconds, interval_seconds)
    asa = average_speed_of_answer(
        float(result["waiting_probability"]),
        aht_seconds,
        raw_agents,
        traffic,
    )
    return {
        "traffic_erlangs": float(traffic),
        "raw_agents": raw_agents,
        "scheduled_agents": scheduled_agents,
        "service_level": float(result["service_level"]),
        "probability_waiting": float(result["waiting_probability"]),
        "occupancy": float(result["occupancy"]),
        "asa_seconds": float(asa),
    }


def read_cdr_csv(file_path: str | Path) -> pd.DataFrame:
    attempts = [
        {"encoding": "utf-16", "sep": "\t"},
        {"encoding": "utf-8-sig", "sep": ","},
        {"encoding": "latin1", "sep": ","},
    ]
    last_error: Exception | None = None
    for kwargs in attempts:
        try:
            frame = pd.read_csv(
                file_path,
                header=None,
                names=CDR_COLUMNS,
                engine="python",
                dtype=str,
                **kwargs,
            )
            if frame[CDR_COLUMNS[1:]].isna().all().all():
                raise ValueError("The selected separator did not produce seven columns.")
            return frame
        except Exception as exc:
            last_error = exc
    raise ValueError(f"Could not read CDR CSV. Last error: {last_error}")


def preprocess_cdr(
    file_path: str | Path,
    min_duration_seconds: int = 1,
    max_duration_seconds: int = 4 * 3600,
    include_dispositions: Optional[Sequence[str]] = None,
) -> pd.DataFrame:
    if min_duration_seconds < 0:
        raise ValueError("Minimum duration cannot be negative.")
    if max_duration_seconds < min_duration_seconds:
        raise ValueError("Maximum duration must be >= minimum duration.")

    allowed = {value.casefold() for value in (include_dispositions or ["Answered"])}
    frame = read_cdr_csv(file_path)
    frame["call_datetime"] = pd.to_datetime(
        frame["call_datetime"],
        format="%Y-%b-%d %I:%M:%S %p",
        errors="coerce",
    )
    frame["duration_seconds"] = frame["duration"].map(hms_to_seconds)

    source_ok = frame["source"].notna() & frame["source"].astype(str).str.strip().ne("")
    destination_text = frame["destination"].fillna("").astype(str).str.strip()
    destination_ok = destination_text.ne("") & destination_text.str.casefold().ne("s")
    disposition_ok = (
        frame["disposition"].fillna("").astype(str).str.strip().str.casefold().isin(allowed)
    )

    clean = frame.loc[
        frame["call_datetime"].notna()
        & frame["duration_seconds"].notna()
        & source_ok
        & destination_ok
        & disposition_ok
    ].copy()
    clean["duration_seconds"] = clean["duration_seconds"].astype(int)
    clean = clean.loc[
        clean["duration_seconds"].between(min_duration_seconds, max_duration_seconds)
    ]
    return clean.sort_values("call_datetime").reset_index(drop=True)


def infer_single_year(clean_df: pd.DataFrame, filename: str = "dataset") -> int:
    years = sorted(clean_df["call_datetime"].dt.year.dropna().astype(int).unique().tolist())
    if len(years) != 1:
        raise ValueError(f"{filename} must contain exactly one calendar year; found {years or 'none'}.")
    return int(years[0])


def build_interval_forecast(
    clean_df: pd.DataFrame,
    interval_minutes: int = 60,
    include_empty_intervals: bool = False,
    year: int | None = None,
) -> pd.DataFrame:
    if clean_df.empty:
        raise ValueError("No clean rows available for interval forecast.")
    interval_minutes = int(interval_minutes)
    if interval_minutes <= 0 or 1440 % interval_minutes != 0:
        raise ValueError("interval_minutes must be a positive divisor of 1440.")

    rule = f"{interval_minutes}min"
    interval_df = (
        clean_df.set_index("call_datetime")
        .resample(rule)
        .agg(
            call_volume=("duration_seconds", "size"),
            total_handle_time_seconds=("duration_seconds", "sum"),
        )
        .reset_index()
    )

    if include_empty_intervals:
        resolved_year = int(year or infer_single_year(clean_df))
        full_index = pd.date_range(
            start=f"{resolved_year}-01-01 00:00:00",
            end=f"{resolved_year + 1}-01-01 00:00:00",
            freq=rule,
            inclusive="left",
        )
        interval_df = (
            interval_df.set_index("call_datetime")
            .reindex(full_index, fill_value=0)
            .rename_axis("call_datetime")
            .reset_index()
        )
    else:
        interval_df = interval_df.loc[interval_df["call_volume"] > 0].copy()

    interval_df["call_volume"] = interval_df["call_volume"].astype(int)
    interval_df["total_handle_time_seconds"] = (
        interval_df["total_handle_time_seconds"].fillna(0).astype(float)
    )
    interval_df["aht_seconds"] = (
        interval_df["total_handle_time_seconds"]
        .div(interval_df["call_volume"].replace(0, pd.NA))
        .astype("Float64")
    )
    interval_df["interval_seconds"] = interval_minutes * 60
    return interval_df


def build_daily_average_pattern(
    file_paths: Sequence[str | Path],
    filenames: Sequence[str] | None = None,
    interval_minutes: int = 30,
) -> tuple[pd.DataFrame, dict]:
    """Average matching calendar day and time intervals across any number of yearly files."""
    if len(file_paths) < 2:
        raise ValueError("Upload at least two full-year CDR datasets.")
    if filenames is not None and len(filenames) != len(file_paths):
        raise ValueError("filenames and file_paths must have the same length.")

    all_intervals: list[pd.DataFrame] = []
    dataset_summary: list[dict] = []
    seen_years: set[int] = set()
    global_handle_time = 0.0
    global_answered_calls = 0

    for index, path in enumerate(file_paths):
        filename = filenames[index] if filenames else Path(path).name
        raw_count = len(read_cdr_csv(path))
        clean = preprocess_cdr(path)
        if clean.empty:
            raise ValueError(f"No valid Answered records found in {filename}.")
        year = infer_single_year(clean, filename)
        if year in seen_years:
            raise ValueError(f"Duplicate year {year}. Upload only one dataset for each year.")
        seen_years.add(year)

        intervals = build_interval_forecast(
            clean,
            interval_minutes=interval_minutes,
            include_empty_intervals=True,
            year=year,
        )
        # The final result is always a 365-day model, so February 29 is excluded.
        intervals = intervals.loc[
            ~((intervals["call_datetime"].dt.month == 2) & (intervals["call_datetime"].dt.day == 29))
        ].copy()
        intervals["source_year"] = year
        intervals["month"] = intervals["call_datetime"].dt.month
        intervals["day"] = intervals["call_datetime"].dt.day
        intervals["hour"] = intervals["call_datetime"].dt.hour
        intervals["minute"] = intervals["call_datetime"].dt.minute
        all_intervals.append(intervals)

        answered_calls = int(len(clean))
        total_handle = float(clean["duration_seconds"].sum())
        global_answered_calls += answered_calls
        global_handle_time += total_handle
        dataset_summary.append(
            {
                "filename": filename,
                "year": year,
                "raw_rows": int(raw_count),
                "valid_answered_calls": answered_calls,
                "removed_rows": int(raw_count - answered_calls),
                "average_aht_seconds": round(total_handle / answered_calls, 2),
                "first_call": clean["call_datetime"].min().isoformat(),
                "last_call": clean["call_datetime"].max().isoformat(),
            }
        )

    historical = pd.concat(all_intervals, ignore_index=True)
    keys = ["month", "day", "hour", "minute"]
    pattern = (
        historical.groupby(keys, as_index=False)
        .agg(
            predicted_call_volume=("call_volume", "mean"),
            total_handle_time_seconds=("total_handle_time_seconds", "sum"),
            total_answered_calls=("call_volume", "sum"),
            dataset_observations=("source_year", "nunique"),
        )
    )

    global_aht = global_handle_time / global_answered_calls
    pattern["predicted_aht_seconds"] = (
        pattern["total_handle_time_seconds"]
        .div(pattern["total_answered_calls"].replace(0, pd.NA))
        .fillna(global_aht)
        .astype(float)
    )
    pattern["predicted_call_volume"] = (
        pattern["predicted_call_volume"].round().clip(lower=0).astype(int)
    )

    return pattern, {
        "dataset_count": len(file_paths),
        "historical_years": sorted(seen_years),
        "datasets": sorted(dataset_summary, key=lambda item: item["year"]),
        "total_valid_answered_calls": int(global_answered_calls),
        "global_weighted_aht_seconds": round(global_aht, 2),
    }


@lru_cache(maxsize=100_000)
def _cached_required_agents(
    call_volume: int,
    aht_seconds_rounded: float,
    interval_seconds: int,
    target_seconds: float,
    target_service_level: float,
    shrinkage: float,
    max_agents: int,
) -> tuple:
    result = required_agents(
        call_volume=call_volume,
        aht_seconds=aht_seconds_rounded,
        interval_seconds=interval_seconds,
        target_seconds=target_seconds,
        target_service_level=target_service_level,
        shrinkage=shrinkage,
        max_agents=max_agents,
    )
    return (
        result["traffic_erlangs"],
        result["raw_agents"],
        result["scheduled_agents"],
        result["service_level"],
        result["probability_waiting"],
        result["occupancy"],
        result["asa_seconds"],
    )


def _automatic_output_year(historical_years: Sequence[int]) -> int:
    return max(map(int, historical_years)) + 1


def build_multi_dataset_forecast(
    file_paths: Sequence[str | Path],
    filenames: Sequence[str] | None = None,
    interval_minutes: int = 30,
    target_seconds: float = 20,
    target_service_level: float = 80,
    shrinkage: float = 30,
    max_agents: int = 1000,
) -> tuple[pd.DataFrame, dict]:
    """Create one automatic 365-day average dataset and run Erlang C on every interval."""
    pattern, source_summary = build_daily_average_pattern(
        file_paths=file_paths,
        filenames=filenames,
        interval_minutes=interval_minutes,
    )
    output_year = _automatic_output_year(source_summary["historical_years"])
    interval_minutes = int(interval_minutes)
    rule = f"{interval_minutes}min"

    dates = pd.date_range(
        start=f"{output_year}-01-01 00:00:00",
        end=f"{output_year + 1}-01-01 00:00:00",
        freq=rule,
        inclusive="left",
    )
    dates = dates[~((dates.month == 2) & (dates.day == 29))]
    forecast = pd.DataFrame({"interval_start": dates})
    forecast["month"] = forecast["interval_start"].dt.month
    forecast["day"] = forecast["interval_start"].dt.day
    forecast["hour"] = forecast["interval_start"].dt.hour
    forecast["minute"] = forecast["interval_start"].dt.minute

    forecast = forecast.merge(
        pattern[[
            "month",
            "day",
            "hour",
            "minute",
            "predicted_call_volume",
            "predicted_aht_seconds",
        ]],
        on=["month", "day", "hour", "minute"],
        how="left",
        validate="many_to_one",
    )
    if forecast[["predicted_call_volume", "predicted_aht_seconds"]].isna().any().any():
        raise RuntimeError("The uploaded datasets did not cover all 365 calendar days.")

    interval_seconds = interval_minutes * 60
    metrics_rows = []
    for row in forecast.itertuples(index=False):
        metrics_rows.append(
            _cached_required_agents(
                int(row.predicted_call_volume),
                round(float(row.predicted_aht_seconds), 2),
                interval_seconds,
                float(target_seconds),
                float(target_service_level),
                float(shrinkage),
                int(max_agents),
            )
        )

    metrics = pd.DataFrame(
        metrics_rows,
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
    forecast = pd.concat([forecast.reset_index(drop=True), metrics], axis=1)
    forecast["date"] = forecast["interval_start"].dt.strftime("%Y-%m-%d")
    forecast["day_of_year"] = range(1, 366) if interval_minutes == 1440 else (
        forecast["interval_start"].dt.normalize().factorize()[0] + 1
    )
    forecast["weekday"] = forecast["interval_start"].dt.dayofweek
    forecast["weekday_name"] = forecast["interval_start"].dt.day_name()
    forecast = forecast.rename(
        columns={
            "predicted_call_volume": "call_volume",
            "predicted_aht_seconds": "aht_seconds",
        }
    )
    forecast["service_level_percent"] = forecast.pop("service_level") * 100
    forecast["probability_waiting_percent"] = forecast.pop("probability_waiting") * 100
    forecast["occupancy_percent"] = forecast.pop("occupancy") * 100
    forecast = forecast.round(
        {
            "aht_seconds": 2,
            "traffic_erlangs": 4,
            "service_level_percent": 2,
            "probability_waiting_percent": 2,
            "occupancy_percent": 2,
            "asa_seconds": 2,
        }
    )
    forecast = forecast[FORECAST_COLUMNS]

    total_calls = int(forecast["call_volume"].sum())
    target_percent = clean_percent(target_service_level) * 100
    peak_row = forecast.loc[forecast["call_volume"].idxmax()]
    summary = {
        "logic": "Average matching month/day/time intervals across all uploaded yearly datasets.",
        "dataset_count": source_summary["dataset_count"],
        "historical_years": source_summary["historical_years"],
        "output_year": output_year,
        "days": 365,
        "interval_minutes": interval_minutes,
        "forecast_interval_count": int(len(forecast)),
        "total_predicted_calls": total_calls,
        "average_aht_seconds": round(
            float((forecast["aht_seconds"] * forecast["call_volume"]).sum()) / max(total_calls, 1),
            2,
        ),
        "maximum_raw_agents": int(forecast["raw_agents"].max()),
        "maximum_scheduled_agents": int(forecast["scheduled_agents"].max()),
        "average_service_level_percent": round(float(forecast["service_level_percent"].mean()), 2),
        "average_occupancy_percent": round(float(forecast["occupancy_percent"].mean()), 2),
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
    }
    return forecast, summary


# Backward-compatible name used by older code.

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
) -> tuple[pd.DataFrame, dict]:
    """Forecast call volume with STL and run Erlang C for each future interval.

    The uploaded files must each contain one unique calendar year. The historical
    series is rebuilt on a continuous interval grid. STL models a weekly cycle by
    default. A linear regression over the most recent trend window extends the STL
    trend, while the final seasonal cycle is repeated into the forecast horizon.
    """
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

    for index, path in enumerate(file_paths):
        filename = filenames[index] if filenames else Path(path).name
        raw_count = len(read_cdr_csv(path))
        clean = preprocess_cdr(path)
        if clean.empty:
            raise ValueError(f"No valid Answered records found in {filename}.")
        year = infer_single_year(clean, filename)
        if year in seen_years:
            raise ValueError(f"Duplicate year {year}. Upload only one dataset for each year.")
        seen_years.add(year)

        frame = build_interval_forecast(
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

    historical = pd.concat(dataset_frames, ignore_index=True).sort_values("interval_start")
    historical = historical.reset_index(drop=True)
    if len(historical) < resolved_period * 2:
        raise ValueError(
            f"STL needs at least two complete seasonal cycles ({resolved_period * 2} intervals)."
        )

    call_series = historical["call_volume"].astype(float)
    decomposition = STL(call_series, period=resolved_period, robust=True).fit()

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

    interval_seconds = interval_minutes * 60
    metrics_rows = []
    for row in future.itertuples(index=False):
        metrics_rows.append(_cached_required_agents(
            int(row.call_volume), round(float(row.aht_seconds), 2), interval_seconds,
            float(target_seconds), float(target_service_level), float(shrinkage), int(max_agents)
        ))
    metrics = pd.DataFrame(metrics_rows, columns=[
        "traffic_erlangs", "raw_agents", "scheduled_agents", "service_level",
        "probability_waiting", "occupancy", "asa_seconds",
    ])
    forecast = pd.concat([future.drop(columns=["slot"]).reset_index(drop=True), metrics], axis=1)
    forecast["date"] = forecast["interval_start"].dt.strftime("%Y-%m-%d")
    forecast["day_of_year"] = forecast["interval_start"].dt.normalize().factorize()[0] + 1
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

def build_multi_year_forecast(
    file_paths: Sequence[str | Path],
    historical_years: Sequence[int] | None = None,
    forecast_year: int | None = None,
    interval_minutes: int = 30,
    target_seconds: float = 20,
    target_service_level: float = 80,
    shrinkage: float = 30,
    max_agents: int = 1000,
) -> tuple[pd.DataFrame, dict]:
    return build_multi_dataset_forecast(
        file_paths=file_paths,
        interval_minutes=interval_minutes,
        target_seconds=target_seconds,
        target_service_level=target_service_level,
        shrinkage=shrinkage,
        max_agents=max_agents,
    )


def build_dashboard_aggregates(forecast: pd.DataFrame) -> dict:
    frame = forecast.copy()
    frame["interval_start"] = pd.to_datetime(frame["interval_start"])

    monthly = (
        frame.assign(
            month_number=frame["interval_start"].dt.month,
            month_label=frame["interval_start"].dt.strftime("%b"),
        )
        .groupby(["month_number", "month_label"], as_index=False)
        .agg(
            call_volume=("call_volume", "sum"),
            max_scheduled_agents=("scheduled_agents", "max"),
            average_occupancy_percent=("occupancy_percent", "mean"),
            average_service_level_percent=("service_level_percent", "mean"),
        )
        .sort_values("month_number")
    )

    daily = (
        frame.groupby("date", as_index=False)
        .agg(
            call_volume=("call_volume", "sum"),
            weighted_handle_time=("aht_seconds", lambda values: 0.0),
            max_scheduled_agents=("scheduled_agents", "max"),
            average_service_level_percent=("service_level_percent", "mean"),
            average_occupancy_percent=("occupancy_percent", "mean"),
            average_asa_seconds=("asa_seconds", "mean"),
        )
    )
    daily = daily.drop(columns=["weighted_handle_time"])

    weekday_order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    weekday = (
        frame.groupby("weekday_name", as_index=False)
        .agg(
            average_calls=("call_volume", "mean"),
            max_scheduled_agents=("scheduled_agents", "max"),
        )
        .set_index("weekday_name")
        .reindex(weekday_order)
        .reset_index()
    )

    frame["time_label"] = frame["interval_start"].dt.strftime("%H:%M")
    time_of_day = (
        frame.groupby("time_label", as_index=False)
        .agg(
            average_calls=("call_volume", "mean"),
            average_scheduled_agents=("scheduled_agents", "mean"),
        )
        .sort_values("time_label")
    )

    def records(df: pd.DataFrame) -> list[dict]:
        return df.round(2).where(pd.notna(df), None).to_dict(orient="records")

    return {
        "monthly": records(monthly.drop(columns=["month_number"])),
        "daily": records(daily),
        "weekday": records(weekday),
        "time_of_day": records(time_of_day),
    }


def process_cdr_for_erlang(
    file_path: str | Path,
    interval_minutes: int,
    target_seconds: float,
    target_service_level: float,
    shrinkage: float,
    output_path: str | Path = "erlang_c_output.csv",
) -> pd.DataFrame:
    clean = preprocess_cdr(file_path)
    intervals = build_interval_forecast(clean, interval_minutes=interval_minutes)
    rows = []
    for row in intervals.itertuples(index=False):
        result = required_agents(
            float(row.call_volume),
            float(row.aht_seconds),
            float(row.interval_seconds),
            target_seconds,
            target_service_level,
            shrinkage,
        )
        rows.append(
            {
                "interval_start": row.call_datetime,
                "call_volume": int(row.call_volume),
                "aht_seconds": round(float(row.aht_seconds), 2),
                "traffic_erlangs": round(result["traffic_erlangs"], 4),
                "raw_agents": int(result["raw_agents"]),
                "scheduled_agents": int(result["scheduled_agents"]),
                "service_level_percent": round(result["service_level"] * 100, 2),
                "probability_waiting_percent": round(result["probability_waiting"] * 100, 2),
                "occupancy_percent": round(result["occupancy"] * 100, 2),
                "asa_seconds": round(result["asa_seconds"], 2),
            }
        )
    output = pd.DataFrame(rows)
    output.to_csv(output_path, index=False)
    return output
