from __future__ import annotations

import pandas as pd
import tempfile
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from calculator import (
    build_dashboard_aggregates,
    build_stl_forecast,
    build_shift_requirements,
    build_monthly_agent_schedule,
    resolve_agent_leave,
    swap_agent_shifts,
)

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
STATIC_DIR.mkdir(exist_ok=True)
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
MAX_UPLOAD_FILES = 10
MAX_FORECAST_ROWS = 40_000
MAX_SCHEDULE_ROWS = 50_000
MAX_AGENT_COUNT = 10_000
MAX_FORECAST_DAYS = 3_650

app = FastAPI(
    title="CDR STL Forecast & Agent Scheduling API",
    description=(
        "Forecast contact-centre demand from historical CDR data using "
        "STL decomposition, then generate monthly schedules and manage "
        "agent leave and shift swaps."
    ),
    version="4.0.0",
)

class MonthlyScheduleRequest(BaseModel):
    forecast: list[dict] = Field(min_length=1, max_length=MAX_FORECAST_ROWS)
    year: int
    month: int = Field(ge=1, le=12)
    agent_count: int | None = Field(default=None, gt=0, le=MAX_AGENT_COUNT)

class AgentLeaveRequest(BaseModel):
    forecast: list[dict] = Field(min_length=1, max_length=MAX_FORECAST_ROWS)
    schedule: list[dict] = Field(min_length=1, max_length=MAX_SCHEDULE_ROWS)
    agent_id: str = Field(min_length=1, max_length=100)
    leave_date: str = Field(min_length=1, max_length=10)

class AgentShiftSwapRequest(BaseModel):
    schedule: list[dict] = Field(min_length=1, max_length=MAX_SCHEDULE_ROWS)
    agent_1: str = Field(min_length=1, max_length=100)
    agent_2: str = Field(min_length=1, max_length=100)
    swap_date: str = Field(min_length=1, max_length=10)

@app.get("/")
def dashboard():
    index_path = STATIC_DIR / "index.html"
    if index_path.exists():
        return FileResponse(index_path)
    return {
    "message": "CDR STL Forecast & Agent Scheduling API",
    "documentation": "/docs",
}

@app.get("/health")
def health() -> dict:
    return {"status": "healthy", "version": "4.0.0"}

async def _save_upload(upload: UploadFile, destination: Path) -> None:
    total_bytes = 0
    with destination.open("wb") as output:
        while chunk := await upload.read(1024 * 1024):
            total_bytes += len(chunk)
            if total_bytes > MAX_UPLOAD_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail=f"Uploaded files must be {MAX_UPLOAD_BYTES // (1024 * 1024)} MB or smaller.",
                )
            output.write(chunk)
    if total_bytes == 0:
        raise HTTPException(status_code=400, detail=f"{upload.filename or 'Uploaded file'} is empty.")

@app.post("/api/v1/cdr/stl-forecast")
async def stl_forecast(
    files: Annotated[list[UploadFile], File(description="One or more full-year CDR CSV files")],
    interval_minutes: Annotated[int, Form()] = 30,
    forecast_days: Annotated[int, Form()] = 365,
    seasonal_period: Annotated[int | None, Form()] = None,
    trend_lookback_days: Annotated[int, Form()] = 90,
    target_seconds: Annotated[float, Form()] = 20,
    target_service_level: Annotated[float, Form()] = 80,
    shrinkage: Annotated[float, Form()] = 30,
    max_agents: Annotated[int, Form()] = 1000,
    include_forecast_rows: Annotated[bool, Form()] = True,
) -> dict:
    if not files:
        raise HTTPException(status_code=400, detail="Upload at least one yearly CDR dataset.")
    if len(files) > MAX_UPLOAD_FILES:
        raise HTTPException(status_code=400, detail=f"Upload no more than {MAX_UPLOAD_FILES} files at a time.")
    if forecast_days < 1 or forecast_days > MAX_FORECAST_DAYS:
        raise HTTPException(status_code=400, detail=f"forecast_days must be between 1 and {MAX_FORECAST_DAYS}.")
    if max_agents < 1 or max_agents > MAX_AGENT_COUNT:
        raise HTTPException(status_code=400, detail=f"max_agents must be between 1 and {MAX_AGENT_COUNT}.")
    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths: list[Path] = []
            filenames: list[str] = []
            for index, upload in enumerate(files, start=1):
                filename = upload.filename or f"dataset_{index}.csv"
                suffix = Path(filename).suffix or ".csv"
                if suffix.lower() not in {".csv", ".txt"}:
                    raise HTTPException(status_code=400, detail=f"Unsupported file type for {filename}.")
                path = Path(temp_dir) / f"dataset_{index}{suffix}"
                await _save_upload(upload, path)
                paths.append(path)
                filenames.append(filename)

            forecast, summary = build_stl_forecast(
                file_paths=paths,
                filenames=filenames,
                interval_minutes=interval_minutes,
                forecast_days=forecast_days,
                seasonal_period=seasonal_period,
                trend_lookback_days=trend_lookback_days,
                target_seconds=target_seconds,
                target_service_level=target_service_level,
                shrinkage=shrinkage,
                max_agents=max_agents,
            )
            response = {
                **summary,
                "parameters": {
                    "interval_minutes": interval_minutes,
                    "forecast_days": forecast_days,
                    "seasonal_period": summary["seasonal_period"],
                    "trend_lookback_days": trend_lookback_days,
                    "target_seconds": target_seconds,
                    "target_service_level_percent": target_service_level,
                    "shrinkage_percent": shrinkage,
                    "max_agents": max_agents,
                },
                "charts": build_dashboard_aggregates(forecast),
            }
            if include_forecast_rows:
                serializable = forecast.copy()
                serializable["interval_start"] = serializable["interval_start"].dt.strftime("%Y-%m-%dT%H:%M:%S")
                response["forecast"] = serializable.to_dict(orient="records")
            else:
                response["forecast"] = []
            return response
    except HTTPException:
        raise
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"STL forecast failed: {exc}") from exc

@app.post("/api/v1/schedule/monthly")
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

@app.post("/api/v1/schedule/leave")
def apply_agent_leave(request: AgentLeaveRequest) -> dict:
    try:
        forecast = pd.DataFrame(request.forecast)
        schedule = pd.DataFrame(request.schedule)

        if forecast.empty:
            raise ValueError("Forecast data is empty.")

        if schedule.empty:
            raise ValueError("Schedule data is empty.")

        if "interval_start" not in forecast.columns:
            raise ValueError(
                "Forecast must contain interval_start."
            )
        missing_schedule_columns = {"agent_id", "date", "shift_code", "shift", "status"} - set(schedule.columns)
        if missing_schedule_columns:
            raise ValueError(
                f"Schedule is missing required columns: {sorted(missing_schedule_columns)}"
            )

        forecast["interval_start"] = pd.to_datetime(
            forecast["interval_start"]
        )

        leave_date = pd.Timestamp(
            request.leave_date
        )

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
            "schedule": updated_schedule.where(
                pd.notna(updated_schedule),
                None,
            ).to_dict(orient="records"),
        }

    except (ValueError, RuntimeError) as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from exc

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Agent leave processing failed: {exc}",
        ) from exc

@app.post("/api/v1/schedule/swap")
def apply_agent_shift_swap(
    request: AgentShiftSwapRequest,
) -> dict:
    try:
        schedule = pd.DataFrame(
            request.schedule
        )

        if schedule.empty:
            raise ValueError(
                "Schedule is empty."
            )

        updated_schedule, result = (
            swap_agent_shifts(
                schedule=schedule,
                agent_1=request.agent_1,
                agent_2=request.agent_2,
                swap_date=request.swap_date,
            )
        )

        return {
            "result": result,
            "schedule": (
                updated_schedule
                .where(
                    pd.notna(
                        updated_schedule
                    ),
                    None,
                )
                .to_dict(
                    orient="records"
                )
            ),
        }

    except (ValueError, RuntimeError) as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from exc

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=(
                "Shift swap processing failed: "
                f"{exc}"
            ),
        ) from exc

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
