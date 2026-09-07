from __future__ import annotations

import pandas as pd
from fastapi import APIRouter, HTTPException

from app.schemas.schedule import (
    MonthlyScheduleRequest,
    AgentLeaveRequest,
    AgentShiftSwapRequest,
)
from app.core.scheduling import (
    build_shift_requirements,
    build_monthly_agent_schedule,
    resolve_agent_leave,
    swap_agent_shifts,
)

router = APIRouter(prefix="/schedule", tags=["Agent Scheduling"])

@router.post("/monthly")
def monthly_schedule(request: MonthlyScheduleRequest) -> dict:
    try:
        forecast = pd.DataFrame(request.forecast)
        if forecast.empty:
            raise ValueError("Forecast data is empty.")
        missing_columns = {"interval_start", "scheduled_agents"} - set(forecast.columns)
        if missing_columns:
            raise ValueError(f"Forecast is missing required columns: {sorted(missing_columns)}")

        schedule, summary = build_monthly_agent_schedule(
            forecast=forecast,
            year=request.year,
            month=request.month,
            agent_count=request.agent_count,
        )

        return {
            "summary": summary,
            "schedule": schedule.to_dict(orient="records"),
        }
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Monthly schedule generation failed: {exc}",
        ) from exc

@router.post("/leave")
def apply_agent_leave(request: AgentLeaveRequest) -> dict:
    try:
        forecast = pd.DataFrame(request.forecast)
        schedule = pd.DataFrame(request.schedule)

        if forecast.empty:
            raise ValueError("Forecast data is empty.")
        if schedule.empty:
            raise ValueError("Schedule data is empty.")

        if "interval_start" not in forecast.columns:
            raise ValueError("Forecast must contain interval_start.")
        missing_schedule_columns = {"agent_id", "date", "shift_code", "shift", "status"} - set(schedule.columns)
        if missing_schedule_columns:
            raise ValueError(f"Schedule is missing required columns: {sorted(missing_schedule_columns)}")

        forecast["interval_start"] = pd.to_datetime(forecast["interval_start"])
        leave_date = pd.Timestamp(request.leave_date)
        year = int(leave_date.year)
        month = int(leave_date.month)

        shift_requirements = build_shift_requirements(
            forecast=forecast,
            year=year,
            month=month,
        )

        updated_schedule, result = resolve_agent_leave(
            schedule=schedule,
            shift_requirements=shift_requirements,
            agent_id=request.agent_id,
            leave_date=request.leave_date,
        )

        return {
            "result": result,
            "schedule": updated_schedule.where(pd.notna(updated_schedule), None).to_dict(orient="records"),
        }
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Agent leave processing failed: {exc}",
        ) from exc

@router.post("/swap")
def apply_agent_shift_swap(request: AgentShiftSwapRequest) -> dict:
    try:
        schedule = pd.DataFrame(request.schedule)
        if schedule.empty:
            raise ValueError("Schedule is empty.")

        updated_schedule, result = swap_agent_shifts(
            schedule=schedule,
            agent_1=request.agent_1,
            agent_2=request.agent_2,
            swap_date=request.swap_date,
        )

        return {
            "result": result,
            "schedule": updated_schedule.where(pd.notna(updated_schedule), None).to_dict(orient="records"),
        }
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Shift swap processing failed: {exc}",
        ) from exc
