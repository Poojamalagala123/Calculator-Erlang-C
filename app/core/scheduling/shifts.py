from __future__ import annotations
import math
import pandas as pd
from app.core.constants import SHIFT_DEFINITIONS

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

