from __future__ import annotations

import math
from collections import defaultdict
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

def calculate_traffic(call_volume: float, aht_seconds: float, interval_seconds: float) -> float:
    if call_volume < 0:
        raise ValueError("Call volume cannot be negative.")
    if aht_seconds <= 0:
        raise ValueError("AHT seconds must be greater than 0.")
    if interval_seconds <= 0:
        raise ValueError("Interval seconds must be greater than 0.")
    return (call_volume * aht_seconds) / interval_seconds

def average_speed_of_answer(erlang_c: float, aht_seconds: float, agents: int, traffic: float) -> float:
    agents = int(agents)
    if agents <= 0:
        raise ValueError("Agents must be greater than 0.")
    if agents <= traffic:
        return float("inf")
    return erlang_c * aht_seconds / (agents - traffic)

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

def build_cdr_intervals(
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

SHIFT_DEFINITIONS = [
    {
        "code": "NIGHT",
        "name": "Night",
        "start_hour": 0,
        "end_hour": 8,
        "label": "00:00-08:00",
    },
    {
        "code": "MORNING",
        "name": "Morning",
        "start_hour": 8,
        "end_hour": 16,
        "label": "08:00-16:00",
    },
    {
        "code": "EVENING",
        "name": "Evening",
        "start_hour": 16,
        "end_hour": 24,
        "label": "16:00-00:00",
    },
]

def build_shift_requirements(
    forecast: pd.DataFrame,
    year: int | None = None,
    month: int | None = None,
) -> pd.DataFrame:
    
    if forecast.empty:
        raise ValueError("Forecast is empty.")

    if "interval_start" not in forecast.columns:
        raise ValueError("Forecast must contain interval_start.")

    if "scheduled_agents" not in forecast.columns:
        raise ValueError("Forecast must contain scheduled_agents.")

    frame = forecast.copy()
    frame["interval_start"] = pd.to_datetime(frame["interval_start"])

    if year is not None:
        frame = frame.loc[frame["interval_start"].dt.year == int(year)]

    if month is not None:
        if not 1 <= int(month) <= 12:
            raise ValueError("month must be between 1 and 12.")

        frame = frame.loc[frame["interval_start"].dt.month == int(month)]

    if frame.empty:
        raise ValueError("No forecast rows found for the selected month.")

    frame["date"] = frame["interval_start"].dt.date
    frame["hour"] = frame["interval_start"].dt.hour

    rows = []

    for date_value, day_frame in frame.groupby("date"):
        for shift in SHIFT_DEFINITIONS:
            shift_frame = day_frame.loc[
                (day_frame["hour"] >= shift["start_hour"])
                & (day_frame["hour"] < shift["end_hour"])
            ]

            required_agents = (
                int(shift_frame["scheduled_agents"].max())
                if not shift_frame.empty
                else 0
            )

            rows.append(
                {
                    "date": pd.Timestamp(date_value),
                    "weekday": pd.Timestamp(date_value).day_name(),
                    "shift_code": shift["code"],
                    "shift_name": shift["name"],
                    "shift_label": shift["label"],
                    "start_hour": shift["start_hour"],
                    "end_hour": shift["end_hour"],
                    "required_agents": required_agents,
                }
            )

    return pd.DataFrame(rows)

def calculate_schedule_headcount(
    shift_requirements: pd.DataFrame,
    working_days_per_week: int = 5,
) -> int:
    
    if shift_requirements.empty:
        return 0

    if working_days_per_week <= 0 or working_days_per_week > 7:
        raise ValueError("working_days_per_week must be between 1 and 7.")

    frame = shift_requirements.copy()
    frame["date"] = pd.to_datetime(frame["date"])

    required_headcount = 0

    frame["week_start"] = (
        frame["date"]
        - pd.to_timedelta(frame["date"].dt.weekday, unit="D")
    )

    for _, week in frame.groupby("week_start"):
        total_weekly_shift_slots = int(week["required_agents"].sum())

        weekly_capacity_headcount = math.ceil(
            total_weekly_shift_slots / working_days_per_week
        )

        daily_requirements = (
            week.groupby("date")["required_agents"]
            .sum()
        )

        maximum_daily_agents = (
            int(daily_requirements.max())
            if not daily_requirements.empty
            else 0
        )

        required_headcount = max(
            required_headcount,
            weekly_capacity_headcount,
            maximum_daily_agents,
        )

    return required_headcount

def build_monthly_agent_schedule(
    forecast: pd.DataFrame,
    year: int,
    month: int,
    agent_count: int | None = None,
) -> tuple[pd.DataFrame, dict]:
    
    requirements = build_shift_requirements(
        forecast=forecast,
        year=year,
        month=month,
    )

    minimum_agents = calculate_schedule_headcount(
        requirements,
        working_days_per_week=5,
    )

    if agent_count is None:
        agent_count = minimum_agents

    agent_count = int(agent_count)

    if agent_count <= 0:
        raise ValueError("agent_count must be greater than 0.")

    if agent_count < minimum_agents:
        raise ValueError(
            f"At least {minimum_agents} agents are estimated to be required "
            f"for {year}-{month:02d}; received {agent_count}."
        )

    agents = [
        f"Agent {number:03d}"
        for number in range(1, agent_count + 1)
    ]

    total_assignments = defaultdict(int)
    shift_assignments = defaultdict(lambda: defaultdict(int))
    weekly_workdays = defaultdict(lambda: defaultdict(int))
    worked_dates = defaultdict(set)

    schedule_rows = []
    coverage_rows = []

    requirements = requirements.sort_values(
        ["date", "start_hour"]
    ).reset_index(drop=True)

    for requirement in requirements.itertuples(index=False):
        date_value = pd.Timestamp(requirement.date)

        week_start = (
            date_value
            - pd.Timedelta(days=date_value.weekday())
        ).date()

        required = int(requirement.required_agents)

        candidates = [
            agent
            for agent in agents
            if date_value.date() not in worked_dates[agent]
            and weekly_workdays[agent][week_start] < 5
        ]

        candidates.sort(
            key=lambda agent: (
                weekly_workdays[agent][week_start],
                total_assignments[agent],
                shift_assignments[agent][requirement.shift_code],
                agent,
            )
        )

        selected_agents = candidates[:required]

        for agent in selected_agents:
            schedule_rows.append(
                {
                    "agent_id": agent,
                    "date": date_value.strftime("%Y-%m-%d"),
                    "weekday": date_value.day_name(),
                    "shift_code": requirement.shift_code,
                    "shift": requirement.shift_label,
                    "status": "WORK",
                }
            )

            total_assignments[agent] += 1
            shift_assignments[agent][requirement.shift_code] += 1
            weekly_workdays[agent][week_start] += 1
            worked_dates[agent].add(date_value.date())

        assigned = len(selected_agents)

        coverage_rows.append(
            {
                "date": date_value.strftime("%Y-%m-%d"),
                "weekday": date_value.day_name(),
                "shift_code": requirement.shift_code,
                "shift": requirement.shift_label,
                "required_agents": required,
                "assigned_agents": assigned,
                "shortage": max(required - assigned, 0),
                "status": "OK" if assigned >= required else "SHORT",
            }
        )

    all_dates = pd.date_range(
        start=f"{year}-{month:02d}-01",
        end=(
            pd.Timestamp(f"{year}-{month:02d}-01")
            + pd.offsets.MonthEnd(0)
        ),
        freq="D",
    )

    existing_assignments = {
        (row["agent_id"], row["date"])
        for row in schedule_rows
    }

    for agent in agents:
        for date_value in all_dates:
            date_text = date_value.strftime("%Y-%m-%d")

            if (agent, date_text) not in existing_assignments:
                schedule_rows.append(
                    {
                        "agent_id": agent,
                        "date": date_text,
                        "weekday": date_value.day_name(),
                        "shift_code": "OFF",
                        "shift": "OFF",
                        "status": "OFF",
                    }
                )

    schedule = pd.DataFrame(schedule_rows).sort_values(
        ["agent_id", "date"]
    ).reset_index(drop=True)

    coverage = pd.DataFrame(coverage_rows)

    summary = {
        "year": int(year),
        "month": int(month),
        "minimum_agents": int(minimum_agents),
        "agent_count": int(agent_count),
        "shift_hours": 8,
        "working_days_per_week": 5,
        "days_off_per_week": 2,
        "total_required_shift_assignments": int(
            requirements["required_agents"].sum()
        ),
        "total_assigned_shift_assignments": int(
            (schedule["status"] == "WORK").sum()
        ),
        "coverage_shortage": int(
            coverage["shortage"].sum()
        ),
        "coverage_ok": bool(
            coverage["shortage"].sum() == 0
        ),
        "coverage": coverage.to_dict(orient="records"),
    }
    return schedule, summary

def mark_agent_leave(
    schedule: pd.DataFrame,
    agent_id: str,
    leave_date: str,
) -> tuple[pd.DataFrame, dict]:
    
    if schedule.empty:
        raise ValueError("Schedule is empty.")

    required_columns = {
        "agent_id",
        "date",
        "shift_code",
        "shift",
        "status",
    }

    missing_columns = required_columns - set(schedule.columns)

    if missing_columns:
        raise ValueError(
            f"Schedule is missing required columns: {sorted(missing_columns)}"
        )

    schedule = schedule.copy()

    agent_id = str(agent_id).strip()

    leave_date = pd.Timestamp(leave_date).strftime("%Y-%m-%d")

    matching_rows = schedule.loc[
        (schedule["agent_id"].astype(str) == agent_id)
        & (schedule["date"].astype(str) == leave_date)
    ]

    if matching_rows.empty:
        raise ValueError(
            f"No schedule found for agent {agent_id} on {leave_date}."
        )

    if len(matching_rows) > 1:
        raise ValueError(
            f"Multiple schedule rows found for agent {agent_id} on {leave_date}."
        )

    row_index = matching_rows.index[0]

    current_shift_code = str(
        schedule.at[row_index, "shift_code"]
    )

    current_shift = str(
        schedule.at[row_index, "shift"]
    )

    current_status = str(
        schedule.at[row_index, "status"]
    )

    # ---------------------------------------------
    # Agent is already OFF
    # ---------------------------------------------

    if current_shift_code == "OFF" or current_status == "OFF":

        return schedule, {
            "agent_id": agent_id,
            "leave_date": leave_date,
            "leave_required": False,
            "original_shift_code": "OFF",
            "original_shift": "OFF",
            "message": "Agent is already OFF on the selected date.",
        }

    # ---------------------------------------------
    # Agent is already on leave
    # ---------------------------------------------

    if current_shift_code == "LEAVE" or current_status == "LEAVE":

        return schedule, {
            "agent_id": agent_id,
            "leave_date": leave_date,
            "leave_required": False,
            "original_shift_code": schedule.at[
                row_index,
                "original_shift_code",
            ]
            if "original_shift_code" in schedule.columns
            else None,
            "original_shift": schedule.at[
                row_index,
                "original_shift",
            ]
            if "original_shift" in schedule.columns
            else None,
            "message": "Agent is already marked as LEAVE.",
        }

    # ---------------------------------------------
    # Create columns if they don't exist yet
    # ---------------------------------------------

    if "original_shift_code" not in schedule.columns:
        schedule["original_shift_code"] = None

    if "original_shift" not in schedule.columns:
        schedule["original_shift"] = None

    # ---------------------------------------------
    # Save original shift before changing it
    # ---------------------------------------------

    schedule.at[
        row_index,
        "original_shift_code",
    ] = current_shift_code

    schedule.at[
        row_index,
        "original_shift",
    ] = current_shift

    # ---------------------------------------------
    # Mark agent as LEAVE
    # ---------------------------------------------

    schedule.at[
        row_index,
        "shift_code",
    ] = "LEAVE"

    schedule.at[
        row_index,
        "shift",
    ] = "LEAVE"

    schedule.at[
        row_index,
        "status",
    ] = "LEAVE"

    result = {
        "agent_id": agent_id,
        "leave_date": leave_date,
        "leave_required": True,
        "original_shift_code": current_shift_code,
        "original_shift": current_shift,
        "message": (
            f"{agent_id} marked as LEAVE on {leave_date}. "
            f"Original shift was {current_shift}."
        ),
    }

    return schedule, result

def calculate_leave_coverage(
    schedule: pd.DataFrame,
    shift_requirements: pd.DataFrame,
    leave_result: dict,
) -> dict:
    
    if schedule.empty:
        raise ValueError("Schedule is empty.")

    if shift_requirements.empty:
        raise ValueError("Shift requirements are empty.")

    if not leave_result.get("leave_required"):
        return {
            "coverage_required": False,
            "shortage": 0,
            "message": leave_result.get(
                "message",
                "No replacement is required.",
            ),
        }

    agent_id = leave_result["agent_id"]
    leave_date = leave_result["leave_date"]
    original_shift_code = leave_result["original_shift_code"]

    # ---------------------------------------------
    # Find required staffing for this date + shift
    # ---------------------------------------------

    requirements = shift_requirements.copy()

    requirements["date"] = pd.to_datetime(
        requirements["date"]
    ).dt.strftime("%Y-%m-%d")

    requirement_row = requirements.loc[
        (requirements["date"] == leave_date)
        & (
            requirements["shift_code"].astype(str)
            == str(original_shift_code)
        )
    ]

    if requirement_row.empty:
        raise ValueError(
            f"No staffing requirement found for "
            f"{leave_date} / {original_shift_code}."
        )

    required_agents = int(
        requirement_row.iloc[0]["required_agents"]
    )

    # ---------------------------------------------
    # Count agents currently working this shift
    # ---------------------------------------------

    current_schedule = schedule.copy()

    current_schedule["date"] = (
        current_schedule["date"]
        .astype(str)
    )

    working_agents = current_schedule.loc[
        (current_schedule["date"] == leave_date)
        & (
            current_schedule["shift_code"].astype(str)
            == str(original_shift_code)
        )
        & (
            current_schedule["status"].astype(str)
            == "WORK"
        )
    ]

    assigned_agents = len(working_agents)

    # ---------------------------------------------
    # Calculate shortage
    # ---------------------------------------------

    shortage = max(
        required_agents - assigned_agents,
        0,
    )

    coverage_ok = assigned_agents >= required_agents

    result = {
        "agent_id": agent_id,
        "leave_date": leave_date,
        "shift_code": original_shift_code,
        "required_agents": required_agents,
        "assigned_agents": assigned_agents,
        "shortage": shortage,
        "coverage_ok": coverage_ok,
    }

    if coverage_ok:
        result["message"] = (
            f"Coverage is still sufficient for "
            f"{original_shift_code} on {leave_date}. "
            f"Required: {required_agents}, "
            f"Assigned: {assigned_agents}."
        )
    else:
        result["message"] = (
            f"Replacement required for "
            f"{original_shift_code} on {leave_date}. "
            f"Required: {required_agents}, "
            f"Assigned: {assigned_agents}, "
            f"Shortage: {shortage}."
        )

    return result

def find_leave_replacement_candidates(
    schedule: pd.DataFrame,
    leave_result: dict,
    max_working_days_per_week: int = 5,
) -> list[dict]:
    
    if schedule.empty:
        raise ValueError("Schedule is empty.")

    if not leave_result.get("leave_required"):
        return []

    leave_agent = str(
        leave_result["agent_id"]
    )

    leave_date = pd.Timestamp(
        leave_result["leave_date"]
    )

    leave_date_text = leave_date.strftime(
        "%Y-%m-%d"
    )

    target_shift_code = str(
        leave_result["original_shift_code"]
    )

    target_shift = str(
        leave_result["original_shift"]
    )

    frame = schedule.copy()

    frame["date"] = pd.to_datetime(
        frame["date"]
    )

    # -------------------------------------------------
    # Calculate Monday-Sunday leave week
    # -------------------------------------------------

    week_start = (
        leave_date
        - pd.Timedelta(
            days=leave_date.weekday()
        )
    )

    week_end = (
        week_start
        + pd.Timedelta(days=6)
    )

    # -------------------------------------------------
    # Get all agents except leave agent
    # -------------------------------------------------

    agents = sorted(
        agent
        for agent in frame["agent_id"]
        .astype(str)
        .unique()
        if agent != leave_agent
    )

    candidates = []

    for agent in agents:

        agent_rows = frame.loc[
            frame["agent_id"]
            .astype(str)
            == agent
        ]

        # ---------------------------------------------
        # Find schedule on leave date
        # ---------------------------------------------

        day_rows = agent_rows.loc[
            agent_rows["date"]
            == leave_date
        ]

        if day_rows.empty:
            continue

        if len(day_rows) > 1:
            continue

        day_row = day_rows.iloc[0]

        current_status = str(
            day_row["status"]
        )

        current_shift_code = str(
            day_row["shift_code"]
        )

        # ---------------------------------------------
        # Only OFF agents can be selected
        # ---------------------------------------------

        if (
            current_status != "OFF"
            or current_shift_code != "OFF"
        ):
            continue

        # ---------------------------------------------
        # Count unique working days in leave week
        # ---------------------------------------------

        weekly_rows = agent_rows.loc[
            (
                agent_rows["date"]
                >= week_start
            )
            & (
                agent_rows["date"]
                <= week_end
            )
            & (
                agent_rows["status"]
                .astype(str)
                == "WORK"
            )
        ]

        weekly_working_days = int(
            weekly_rows["date"].nunique()
        )

        # ---------------------------------------------
        # Calculate working days after cover
        # ---------------------------------------------

        weekly_days_after_cover = (
            weekly_working_days + 1
        )

        # ---------------------------------------------
        # Reject if cover would exceed 5 days
        # ---------------------------------------------

        if (
            weekly_days_after_cover
            > max_working_days_per_week
        ):
            continue

        # ---------------------------------------------
        # Count unique monthly working days
        # ---------------------------------------------

        monthly_working_days = int(
            agent_rows.loc[
                agent_rows["status"]
                .astype(str)
                == "WORK",
                "date",
            ].nunique()
        )

        # ---------------------------------------------
        # Add valid candidate
        # ---------------------------------------------

        candidates.append(
            {
                "agent_id": agent,
                "leave_date": leave_date_text,

                "target_shift_code":
                    target_shift_code,

                "target_shift":
                    target_shift,

                "current_status": "OFF",

                "weekly_working_days":
                    weekly_working_days,

                "weekly_days_after_cover":
                    weekly_days_after_cover,

                "monthly_working_days":
                    monthly_working_days,
            }
        )

    # -------------------------------------------------
    # Rank candidates fairly
    # -------------------------------------------------

    candidates.sort(
        key=lambda item: (
            item[
                "weekly_working_days"
            ],
            item[
                "monthly_working_days"
            ],
            item[
                "agent_id"
            ],
        )
    )

    return candidates

def assign_leave_replacement(
    schedule: pd.DataFrame,
    leave_result: dict,
    coverage_result: dict,
    candidates: list[dict],
) -> tuple[pd.DataFrame, dict]:
   
    if schedule.empty:
        raise ValueError("Schedule is empty.")

    if not leave_result.get("leave_required"):
        return schedule, {
            "replacement_applied": False,
            "message": "No leave replacement is required.",
        }

    if coverage_result.get("coverage_ok"):
        return schedule, {
            "replacement_applied": False,
            "message": (
                "Coverage is already sufficient. "
                "No replacement is required."
            ),
        }

    if not candidates:
        return schedule, {
            "replacement_applied": False,
            "message": (
                "No eligible OFF agent was found "
                "to cover the leave shift."
            ),
        }

    updated_schedule = schedule.copy()

    replacement = candidates[0]

    replacement_agent = replacement["agent_id"]
    leave_date = leave_result["leave_date"]

    target_shift_code = leave_result[
        "original_shift_code"
    ]

    target_shift = leave_result[
        "original_shift"
    ]

    # -------------------------------------------------
    # Find replacement agent row
    # -------------------------------------------------

    matching_rows = updated_schedule.loc[
        (
            updated_schedule["agent_id"].astype(str)
            == str(replacement_agent)
        )
        & (
            updated_schedule["date"].astype(str)
            == str(leave_date)
        )
    ]

    if matching_rows.empty:
        raise ValueError(
            f"No schedule row found for replacement agent "
            f"{replacement_agent} on {leave_date}."
        )

    if len(matching_rows) > 1:
        raise ValueError(
            f"Multiple schedule rows found for replacement agent "
            f"{replacement_agent} on {leave_date}."
        )

    row_index = matching_rows.index[0]

    current_status = str(
        updated_schedule.at[
            row_index,
            "status",
        ]
    )

    current_shift_code = str(
        updated_schedule.at[
            row_index,
            "shift_code",
        ]
    )

    # -------------------------------------------------
    # Confirm agent is still OFF
    # -------------------------------------------------

    if (
        current_status != "OFF"
        or current_shift_code != "OFF"
    ):
        raise ValueError(
            f"{replacement_agent} is no longer "
            f"OFF on {leave_date}."
        )

    # -------------------------------------------------
    # Keep previous assignment for audit/history
    # -------------------------------------------------

    if (
        "previous_shift_code"
        not in updated_schedule.columns
    ):
        updated_schedule[
            "previous_shift_code"
        ] = None

    if (
        "previous_shift"
        not in updated_schedule.columns
    ):
        updated_schedule[
            "previous_shift"
        ] = None

    if (
        "assignment_type"
        not in updated_schedule.columns
    ):
        updated_schedule[
            "assignment_type"
        ] = None

    updated_schedule.at[
        row_index,
        "previous_shift_code",
    ] = current_shift_code

    updated_schedule.at[
        row_index,
        "previous_shift",
    ] = "OFF"

    # -------------------------------------------------
    # Assign replacement shift
    # -------------------------------------------------

    updated_schedule.at[
        row_index,
        "shift_code",
    ] = target_shift_code

    updated_schedule.at[
        row_index,
        "shift",
    ] = target_shift

    updated_schedule.at[
        row_index,
        "status",
    ] = "WORK"

    updated_schedule.at[
        row_index,
        "assignment_type",
    ] = "LEAVE_COVER"

    # -------------------------------------------------
    # Validate rest period after assignment
    # -------------------------------------------------

    rest_validation = validate_agent_rest_period(
        updated_schedule,
        replacement_agent,
        leave_date,
    )

    if not rest_validation["valid"]:
        raise ValueError(
            rest_validation["message"]
        )

    # -------------------------------------------------
    # Build result
    # -------------------------------------------------

    result = {
        "replacement_applied": True,
        "leave_agent": leave_result[
            "agent_id"
        ],
        "replacement_agent": replacement_agent,
        "leave_date": leave_date,
        "shift_code": target_shift_code,
        "shift": target_shift,

        "weekly_working_days_before":
            replacement[
                "weekly_working_days"
            ],

        "weekly_working_days_after":
            replacement[
                "weekly_days_after_cover"
            ],

        "monthly_working_days_before":
            replacement[
                "monthly_working_days"
            ],

        "rest_validation":
            rest_validation,

        "message": (
            f"{replacement_agent} assigned to cover "
            f"{target_shift_code} on {leave_date} "
            f"for {leave_result['agent_id']}."
        ),
    }

    return updated_schedule, result

def find_safe_shift_transfer_candidates(
    schedule: pd.DataFrame,
    shift_requirements: pd.DataFrame,
    leave_result: dict,
) -> list[dict]:
   
    if schedule.empty:
        raise ValueError("Schedule is empty.")

    if shift_requirements.empty:
        raise ValueError("Shift requirements are empty.")

    if not leave_result.get("leave_required"):
        return []

    frame = schedule.copy()

    frame["date"] = pd.to_datetime(
        frame["date"]
    ).dt.strftime("%Y-%m-%d")

    requirements = shift_requirements.copy()

    requirements["date"] = pd.to_datetime(
        requirements["date"]
    ).dt.strftime("%Y-%m-%d")

    leave_date = leave_result["leave_date"]
    leave_agent = leave_result["agent_id"]

    target_shift_code = leave_result[
        "original_shift_code"
    ]

    target_shift = leave_result[
        "original_shift"
    ]

    # ---------------------------------------------
    # Agents working on the leave date
    # but not already in the target shift
    # ---------------------------------------------

    working_rows = frame.loc[
        (frame["date"] == leave_date)
        & (frame["status"].astype(str) == "WORK")
        & (
            frame["shift_code"].astype(str)
            != str(target_shift_code)
        )
        & (
            frame["agent_id"].astype(str)
            != str(leave_agent)
        )
    ]

    candidates = []

    for row in working_rows.itertuples(index=False):

        source_shift_code = str(
            row.shift_code
        )

        source_shift = str(
            row.shift
        )

        # -----------------------------------------
        # Required staffing in source shift
        # -----------------------------------------

        source_requirement = requirements.loc[
            (requirements["date"] == leave_date)
            & (
                requirements["shift_code"].astype(str)
                == source_shift_code
            )
        ]

        if source_requirement.empty:
            continue

        required_agents = int(
            source_requirement.iloc[0][
                "required_agents"
            ]
        )

        # -----------------------------------------
        # Current agents in source shift
        # -----------------------------------------

        source_working = frame.loc[
            (frame["date"] == leave_date)
            & (
                frame["shift_code"].astype(str)
                == source_shift_code
            )
            & (
                frame["status"].astype(str)
                == "WORK"
            )
        ]

        assigned_agents = len(
            source_working
        )

        assigned_after_move = (
            assigned_agents - 1
        )

        spare_agents = (
            assigned_agents - required_agents
        )

        # -----------------------------------------
        # Reject if moving this agent creates
        # a shortage in their original shift
        # -----------------------------------------

        if assigned_after_move < required_agents:
            continue

        # -----------------------------------------
        # Monthly working days
        # -----------------------------------------

        agent_rows = frame.loc[
            frame["agent_id"].astype(str)
            == str(row.agent_id)
        ]

        monthly_working_days = len(
            agent_rows.loc[
                agent_rows["status"].astype(str)
                == "WORK"
            ]
        )

        candidates.append(
            {
                "agent_id": row.agent_id,
                "leave_date": leave_date,
                "target_shift_code": target_shift_code,
                "target_shift": target_shift,
                "source_shift_code": source_shift_code,
                "source_shift": source_shift,
                "source_required_agents": required_agents,
                "source_assigned_agents": assigned_agents,
                "source_assigned_after_move": assigned_after_move,
                "source_spare_agents": spare_agents,
                "monthly_working_days": monthly_working_days,
            }
        )

    # ---------------------------------------------
    # Ranking:
    # 1. shifts with more spare agents
    # 2. agents with fewer monthly working days
    # 3. agent id
    # ---------------------------------------------

    candidates.sort(
        key=lambda item: (
            -item["source_spare_agents"],
            item["monthly_working_days"],
            item["agent_id"],
        )
    )

    return candidates

def assign_safe_shift_transfer(
    schedule: pd.DataFrame,
    leave_result: dict,
    transfer_candidates: list[dict],
) -> tuple[pd.DataFrame, dict]:
    
    if schedule.empty:
        raise ValueError("Schedule is empty.")

    if not transfer_candidates:
        return schedule, {
            "transfer_applied": False,
            "message": (
                "No safe shift-transfer candidate "
                "was found."
            ),
        }

    updated_schedule = schedule.copy()

    candidate = transfer_candidates[0]

    agent_id = candidate["agent_id"]
    leave_date = leave_result["leave_date"]

    # -------------------------------------------------
    # Find transfer agent row
    # -------------------------------------------------

    row = updated_schedule.loc[
        (
            updated_schedule["agent_id"]
            .astype(str)
            == str(agent_id)
        )
        & (
            updated_schedule["date"]
            .astype(str)
            == str(leave_date)
        )
    ]

    if row.empty:
        raise ValueError(
            f"No schedule found for {agent_id} "
            f"on {leave_date}."
        )

    if len(row) > 1:
        raise ValueError(
            f"Multiple schedule rows found for "
            f"{agent_id} on {leave_date}."
        )

    row_index = row.index[0]

    # -------------------------------------------------
    # Audit columns
    # -------------------------------------------------

    if (
        "previous_shift_code"
        not in updated_schedule.columns
    ):
        updated_schedule[
            "previous_shift_code"
        ] = None

    if (
        "previous_shift"
        not in updated_schedule.columns
    ):
        updated_schedule[
            "previous_shift"
        ] = None

    if (
        "assignment_type"
        not in updated_schedule.columns
    ):
        updated_schedule[
            "assignment_type"
        ] = None

    # -------------------------------------------------
    # Store original shift
    # -------------------------------------------------

    updated_schedule.at[
        row_index,
        "previous_shift_code",
    ] = candidate["source_shift_code"]

    updated_schedule.at[
        row_index,
        "previous_shift",
    ] = candidate["source_shift"]

    # -------------------------------------------------
    # Move agent to leave shift
    # -------------------------------------------------

    updated_schedule.at[
        row_index,
        "shift_code",
    ] = leave_result[
        "original_shift_code"
    ]

    updated_schedule.at[
        row_index,
        "shift",
    ] = leave_result[
        "original_shift"
    ]

    updated_schedule.at[
        row_index,
        "status",
    ] = "WORK"

    updated_schedule.at[
        row_index,
        "assignment_type",
    ] = "SHIFT_TRANSFER"

    # -------------------------------------------------
    # Validate rest period after transfer
    # -------------------------------------------------

    rest_validation = validate_agent_rest_period(
        updated_schedule,
        agent_id,
        leave_date,
    )

    if not rest_validation["valid"]:
        raise ValueError(
            rest_validation["message"]
        )

    # -------------------------------------------------
    # Build result
    # -------------------------------------------------

    result = {
        "transfer_applied": True,

        "leave_agent":
            leave_result["agent_id"],

        "replacement_agent":
            agent_id,

        "leave_date":
            leave_date,

        "source_shift_code":
            candidate[
                "source_shift_code"
            ],

        "target_shift_code":
            leave_result[
                "original_shift_code"
            ],

        "source_required_agents":
            candidate[
                "source_required_agents"
            ],

        "source_assigned_after_move":
            candidate[
                "source_assigned_after_move"
            ],

        "rest_validation":
            rest_validation,

        "message": (
            f"{agent_id} moved from "
            f"{candidate['source_shift_code']} "
            f"to {leave_result['original_shift_code']} "
            f"on {leave_date}."
        ),
    }

    return updated_schedule, result

def resolve_agent_leave(
    schedule: pd.DataFrame,
    shift_requirements: pd.DataFrame,
    agent_id: str,
    leave_date: str,
) -> tuple[pd.DataFrame, dict]:
    
    if schedule.empty:
        raise ValueError("Schedule is empty.")

    if shift_requirements.empty:
        raise ValueError("Shift requirements are empty.")

    # -------------------------------------------------
    # Step 1: Mark requested agent as leave
    # -------------------------------------------------

    updated_schedule, leave_result = mark_agent_leave(
        schedule=schedule,
        agent_id=agent_id,
        leave_date=leave_date,
    )

    # -------------------------------------------------
    # Step 2: Check coverage after leave
    # -------------------------------------------------

    coverage_before = calculate_leave_coverage(
        schedule=updated_schedule,
        shift_requirements=shift_requirements,
        leave_result=leave_result,
    )

    # -------------------------------------------------
    # If agent was already OFF / no leave needed
    # -------------------------------------------------

    if not leave_result.get("leave_required"):

        return updated_schedule, {
            "resolved": True,
            "method": "NO_ACTION",
            "leave": leave_result,
            "coverage_before": coverage_before,
            "coverage_after": coverage_before,
            "message": leave_result.get(
                "message",
                "No action required.",
            ),
        }

    # -------------------------------------------------
    # If coverage is still okay after leave
    # -------------------------------------------------

    if coverage_before.get("coverage_ok"):

        return updated_schedule, {
            "resolved": True,
            "method": "NO_REPLACEMENT_REQUIRED",
            "leave": leave_result,
            "coverage_before": coverage_before,
            "coverage_after": coverage_before,
            "message": (
                "Leave approved. Existing staffing "
                "is still sufficient."
            ),
        }

    # -------------------------------------------------
    # Step 3: Try OFF-agent replacement
    # -------------------------------------------------

    off_candidates = find_leave_replacement_candidates(
        schedule=updated_schedule,
        leave_result=leave_result,
    )

    if off_candidates:

        updated_schedule, replacement_result = (
            assign_leave_replacement(
                schedule=updated_schedule,
                leave_result=leave_result,
                coverage_result=coverage_before,
                candidates=off_candidates,
            )
        )

        coverage_after = calculate_leave_coverage(
            schedule=updated_schedule,
            shift_requirements=shift_requirements,
            leave_result=leave_result,
        )

        if coverage_after.get("coverage_ok"):

            return updated_schedule, {
                "resolved": True,
                "method": "OFF_AGENT_COVER",
                "leave": leave_result,
                "coverage_before": coverage_before,
                "coverage_after": coverage_after,
                "replacement": replacement_result,
                "message": (
                    f"Leave resolved using OFF agent "
                    f"{replacement_result['replacement_agent']}."
                ),
            }

    # -------------------------------------------------
    # Step 4: Try safe shift transfer
    # -------------------------------------------------

    transfer_candidates = find_safe_shift_transfer_candidates(
        schedule=updated_schedule,
        shift_requirements=shift_requirements,
        leave_result=leave_result,
    )

    if transfer_candidates:

        updated_schedule, transfer_result = (
            assign_safe_shift_transfer(
                schedule=updated_schedule,
                leave_result=leave_result,
                transfer_candidates=transfer_candidates,
            )
        )

        coverage_after = calculate_leave_coverage(
            schedule=updated_schedule,
            shift_requirements=shift_requirements,
            leave_result=leave_result,
        )

        if coverage_after.get("coverage_ok"):

            return updated_schedule, {
                "resolved": True,
                "method": "SHIFT_TRANSFER",
                "leave": leave_result,
                "coverage_before": coverage_before,
                "coverage_after": coverage_after,
                "transfer": transfer_result,
                "message": (
                    f"Leave resolved by moving "
                    f"{transfer_result['replacement_agent']} "
                    f"from "
                    f"{transfer_result['source_shift_code']} "
                    f"to "
                    f"{transfer_result['target_shift_code']}."
                ),
            }

    # -------------------------------------------------
    # Step 5: No safe option found
    # -------------------------------------------------

    final_coverage = calculate_leave_coverage(
        schedule=updated_schedule,
        shift_requirements=shift_requirements,
        leave_result=leave_result,
    )

    return updated_schedule, {
        "resolved": False,
        "method": "UNRESOLVED",
        "leave": leave_result,
        "coverage_before": coverage_before,
        "coverage_after": final_coverage,
        "message": (
            "Leave could not be covered safely. "
            "No eligible OFF agent or safe shift-transfer "
            "candidate was available."
        ),
    }

def swap_agent_shifts(
    schedule: pd.DataFrame,
    agent_1: str,
    agent_2: str,
    swap_date: str,
) -> tuple[pd.DataFrame, dict]:
    
    if schedule.empty:
        raise ValueError("Schedule is empty.")

    updated_schedule = schedule.copy()

    agent_1 = str(agent_1).strip()
    agent_2 = str(agent_2).strip()

    if agent_1 == agent_2:
        raise ValueError(
            "Select two different agents for a shift swap."
        )

    swap_date = pd.Timestamp(
        swap_date
    ).strftime("%Y-%m-%d")

    # ---------------------------------------------
    # Find Agent 1 schedule row
    # ---------------------------------------------

    agent_1_rows = updated_schedule.loc[
        (
            updated_schedule["agent_id"].astype(str)
            == agent_1
        )
        & (
            updated_schedule["date"].astype(str)
            == swap_date
        )
    ]

    if agent_1_rows.empty:
        raise ValueError(
            f"No schedule found for {agent_1} on {swap_date}."
        )

    if len(agent_1_rows) > 1:
        raise ValueError(
            f"Multiple schedule rows found for "
            f"{agent_1} on {swap_date}."
        )

    # ---------------------------------------------
    # Find Agent 2 schedule row
    # ---------------------------------------------

    agent_2_rows = updated_schedule.loc[
        (
            updated_schedule["agent_id"].astype(str)
            == agent_2
        )
        & (
            updated_schedule["date"].astype(str)
            == swap_date
        )
    ]

    if agent_2_rows.empty:
        raise ValueError(
            f"No schedule found for {agent_2} on {swap_date}."
        )

    if len(agent_2_rows) > 1:
        raise ValueError(
            f"Multiple schedule rows found for "
            f"{agent_2} on {swap_date}."
        )

    index_1 = agent_1_rows.index[0]
    index_2 = agent_2_rows.index[0]

    # ---------------------------------------------
    # Read current assignments
    # ---------------------------------------------

    status_1 = str(
        updated_schedule.at[index_1, "status"]
    )

    status_2 = str(
        updated_schedule.at[index_2, "status"]
    )

    shift_code_1 = str(
        updated_schedule.at[index_1, "shift_code"]
    )

    shift_code_2 = str(
        updated_schedule.at[index_2, "shift_code"]
    )

    shift_1 = str(
        updated_schedule.at[index_1, "shift"]
    )

    shift_2 = str(
        updated_schedule.at[index_2, "shift"]
    )

    # ---------------------------------------------
    # Basic validation
    # ---------------------------------------------

    if status_1 != "WORK":
        raise ValueError(
            f"{agent_1} is not working on {swap_date}."
        )

    if status_2 != "WORK":
        raise ValueError(
            f"{agent_2} is not working on {swap_date}."
        )

    if shift_code_1 in {"OFF", "LEAVE"}:
        raise ValueError(
            f"{agent_1} cannot swap from {shift_code_1}."
        )

    if shift_code_2 in {"OFF", "LEAVE"}:
        raise ValueError(
            f"{agent_2} cannot swap from {shift_code_2}."
        )

    if shift_code_1 == shift_code_2:
        raise ValueError(
            "Both agents already have the same shift."
        )

    # ---------------------------------------------
    # Create audit columns if needed
    # ---------------------------------------------

    if "previous_shift_code" not in updated_schedule.columns:
        updated_schedule["previous_shift_code"] = None

    if "previous_shift" not in updated_schedule.columns:
        updated_schedule["previous_shift"] = None

    if "assignment_type" not in updated_schedule.columns:
        updated_schedule["assignment_type"] = None

    # ---------------------------------------------
    # Store original assignments
    # ---------------------------------------------

    updated_schedule.at[
        index_1,
        "previous_shift_code",
    ] = shift_code_1

    updated_schedule.at[
        index_1,
        "previous_shift",
    ] = shift_1

    updated_schedule.at[
        index_2,
        "previous_shift_code",
    ] = shift_code_2

    updated_schedule.at[
        index_2,
        "previous_shift",
    ] = shift_2

    # ---------------------------------------------
    # Perform swap
    # ---------------------------------------------

    updated_schedule.at[
        index_1,
        "shift_code",
    ] = shift_code_2

    updated_schedule.at[
        index_1,
        "shift",
    ] = shift_2

    updated_schedule.at[
        index_2,
        "shift_code",
    ] = shift_code_1

    updated_schedule.at[
        index_2,
        "shift",
    ] = shift_1

    updated_schedule.at[
        index_1,
        "assignment_type",
    ] = "SHIFT_SWAP"

    updated_schedule.at[
        index_2,
        "assignment_type",
    ] = "SHIFT_SWAP"

    # ---------------------------------------------
    # Validate rest period after swap
    # ---------------------------------------------

    validation_1 = validate_agent_rest_period(
        updated_schedule,
        agent_1,
        swap_date,
    )

    validation_2 = validate_agent_rest_period(
        updated_schedule,
        agent_2,
        swap_date,
    )

    if not validation_1["valid"]:
        raise ValueError(
            validation_1["message"]
        )

    if not validation_2["valid"]:
        raise ValueError(
            validation_2["message"]
        )

    # ---------------------------------------------
    # Build result
    # ---------------------------------------------

    result = {
        "swap_applied": True,
        "date": swap_date,

        "agent_1": agent_1,
        "agent_1_previous_shift": shift_code_1,
        "agent_1_new_shift": shift_code_2,

        "agent_2": agent_2,
        "agent_2_previous_shift": shift_code_2,
        "agent_2_new_shift": shift_code_1,

        "agent_1_rest_validation": validation_1,
        "agent_2_rest_validation": validation_2,

        "message": (
            f"{agent_1} and {agent_2} successfully "
            f"swapped shifts on {swap_date}."
        ),
    }

    return updated_schedule, result

def validate_agent_rest_period(
    schedule: pd.DataFrame,
    agent_id: str,
    target_date: str,
    minimum_rest_hours: int = 8,
) -> dict:
   
    if schedule.empty:
        raise ValueError("Schedule is empty.")

    frame = schedule.copy()

    frame["date"] = pd.to_datetime(
        frame["date"]
    )

    target_date = pd.Timestamp(
        target_date
    )

    agent_rows = frame.loc[
        frame["agent_id"].astype(str)
        == str(agent_id)
    ].sort_values("date")

    target_rows = agent_rows.loc[
        agent_rows["date"] == target_date
    ]

    if target_rows.empty:
        raise ValueError(
            f"No schedule found for {agent_id} on "
            f"{target_date.strftime('%Y-%m-%d')}."
        )

    target_row = target_rows.iloc[0]

    if str(target_row["status"]) != "WORK":
        return {
            "valid": True,
            "message": "Agent is not working on the target date.",
        }

    shift_lookup = {
        "NIGHT": {
            "start": 0,
            "end": 8,
        },
        "MORNING": {
            "start": 8,
            "end": 16,
        },
        "EVENING": {
            "start": 16,
            "end": 24,
        },
    }

    target_shift_code = str(
        target_row["shift_code"]
    )

    if target_shift_code not in shift_lookup:
        return {
            "valid": True,
            "message": "No rest validation required.",
        }

    target_start = (
        target_date
        + pd.Timedelta(
            hours=shift_lookup[target_shift_code]["start"]
        )
    )

    target_end = (
        target_date
        + pd.Timedelta(
            hours=shift_lookup[target_shift_code]["end"]
        )
    )

    previous_date = (
        target_date - pd.Timedelta(days=1)
    )

    next_date = (
        target_date + pd.Timedelta(days=1)
    )

    # ---------------------------------------------
    # Check previous day
    # ---------------------------------------------

    previous_rows = agent_rows.loc[
        agent_rows["date"] == previous_date
    ]

    if not previous_rows.empty:

        previous_row = previous_rows.iloc[0]

        previous_status = str(
            previous_row["status"]
        )

        previous_shift_code = str(
            previous_row["shift_code"]
        )

        if (
            previous_status == "WORK"
            and previous_shift_code in shift_lookup
        ):

            previous_end = (
                previous_date
                + pd.Timedelta(
                    hours=shift_lookup[
                        previous_shift_code
                    ]["end"]
                )
            )

            rest_hours = (
                target_start - previous_end
            ).total_seconds() / 3600

            if rest_hours < minimum_rest_hours:

                return {
                    "valid": False,
                    "reason": "PREVIOUS_SHIFT_REST",
                    "rest_hours": rest_hours,
                    "message": (
                        f"{agent_id} would only have "
                        f"{rest_hours:.1f} hours rest "
                        f"before the new shift."
                    ),
                }

    # ---------------------------------------------
    # Check next day
    # ---------------------------------------------

    next_rows = agent_rows.loc[
        agent_rows["date"] == next_date
    ]

    if not next_rows.empty:

        next_row = next_rows.iloc[0]

        next_status = str(
            next_row["status"]
        )

        next_shift_code = str(
            next_row["shift_code"]
        )

        if (
            next_status == "WORK"
            and next_shift_code in shift_lookup
        ):

            next_start = (
                next_date
                + pd.Timedelta(
                    hours=shift_lookup[
                        next_shift_code
                    ]["start"]
                )
            )

            rest_hours = (
                next_start - target_end
            ).total_seconds() / 3600

            if rest_hours < minimum_rest_hours:

                return {
                    "valid": False,
                    "reason": "NEXT_SHIFT_REST",
                    "rest_hours": rest_hours,
                    "message": (
                        f"{agent_id} would only have "
                        f"{rest_hours:.1f} hours rest "
                        f"before the next shift."
                    ),
                }

    return {
        "valid": True,
        "message": (
            f"{agent_id} satisfies the minimum "
            f"{minimum_rest_hours}-hour rest rule."
        ),
    }