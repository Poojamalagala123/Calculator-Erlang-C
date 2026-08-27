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
    average_speed_of_answer,
    build_dashboard_aggregates,
    build_interval_forecast,
    build_multi_dataset_forecast,
    build_stl_forecast,
    calculate_aht,
    calculate_traffic,
    erlang_c_probability,
    occupancy,
    preprocess_cdr,
    required_agents,
    service_level,
    build_shift_requirements,
    calculate_schedule_headcount,
    build_monthly_agent_schedule,
)

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
STATIC_DIR.mkdir(exist_ok=True)

app = FastAPI(
    title="Erlang C Multi-Dataset Forecast API",
    description="Upload any number of yearly CDR datasets and create one 365-day average Erlang C forecast.",
    version="4.0.0",
)


class AHTRequest(BaseModel):
    total_handle_time_seconds: float = Field(gt=0)
    total_answered_calls: int = Field(gt=0)


class TrafficRequest(BaseModel):
    call_volume: float = Field(ge=0)
    aht_seconds: float = Field(gt=0)
    interval_seconds: float = Field(gt=0)


class ErlangOutputsRequest(BaseModel):
    traffic_erlangs: float = Field(ge=0)
    agents: int = Field(gt=0)
    aht_seconds: float = Field(gt=0)
    target_seconds: float = Field(ge=0)


class RequiredAgentsRequest(BaseModel):
    call_volume: float = Field(ge=0)
    aht_seconds: float = Field(gt=0)
    interval_seconds: float = Field(gt=0)
    target_seconds: float = Field(ge=0)
    target_service_level: float = Field(gt=0)
    shrinkage: float = Field(default=0, ge=0)
    max_agents: int = Field(default=1000, gt=0)

class MonthlyScheduleRequest(BaseModel):
    forecast: list[dict]
    year: int
    month: int = Field(ge=1, le=12)
    agent_count: int | None = Field(default=None, gt=0)

@app.get("/")
def dashboard():
    index_path = STATIC_DIR / "index.html"
    if index_path.exists():
        return FileResponse(index_path)
    return {"message": "Erlang C Multi-Dataset Forecast API", "documentation": "/docs"}


@app.get("/health")
def health() -> dict:
    return {"status": "healthy", "version": "4.0.0"}


@app.post("/api/v1/aht")
def get_aht(request: AHTRequest) -> dict:
    try:
        return {"aht_seconds": round(calculate_aht(
            request.total_handle_time_seconds,
            request.total_answered_calls,
        ), 2)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/v1/traffic")
def get_traffic(request: TrafficRequest) -> dict:
    try:
        return {"traffic_erlangs": round(calculate_traffic(
            request.call_volume,
            request.aht_seconds,
            request.interval_seconds,
        ), 4)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/v1/erlang-outputs")
def get_erlang_outputs(request: ErlangOutputsRequest) -> dict:
    try:
        waiting = erlang_c_probability(request.traffic_erlangs, request.agents)
        asa = average_speed_of_answer(
            waiting, request.aht_seconds, request.agents, request.traffic_erlangs
        )
        sl = service_level(
            waiting,
            request.agents,
            request.traffic_erlangs,
            request.target_seconds,
            request.aht_seconds,
        )
        occ = occupancy(request.traffic_erlangs, request.agents)
        return {
            "traffic_erlangs": request.traffic_erlangs,
            "agents": request.agents,
            "probability_waiting_percent": round(waiting * 100, 2),
            "asa_seconds": None if asa == float("inf") else round(asa, 2),
            "service_level_percent": round(sl * 100, 2),
            "occupancy_percent": round(occ * 100, 2),
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/v1/required-agents")
def get_required_agents(request: RequiredAgentsRequest) -> dict:
    try:
        result = required_agents(
            request.call_volume,
            request.aht_seconds,
            request.interval_seconds,
            request.target_seconds,
            request.target_service_level,
            request.shrinkage,
            request.max_agents,
        )
        return {
            "traffic_erlangs": round(result["traffic_erlangs"], 4),
            "raw_agents": result["raw_agents"],
            "scheduled_agents": result["scheduled_agents"],
            "service_level_percent": round(result["service_level"] * 100, 2),
            "probability_waiting_percent": round(result["probability_waiting"] * 100, 2),
            "occupancy_percent": round(result["occupancy"] * 100, 2),
            "asa_seconds": round(result["asa_seconds"], 2),
        }
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


async def _save_upload(upload: UploadFile, destination: Path) -> None:
    content = await upload.read()
    if not content:
        raise HTTPException(status_code=400, detail=f"{upload.filename or 'Uploaded file'} is empty.")
    destination.write_bytes(content)


@app.post("/api/v1/cdr/forecast")
async def forecast_from_cdr(
    file: Annotated[UploadFile, File(description="CDR CSV file")],
    interval_minutes: Annotated[int, Form()] = 60,
    target_seconds: Annotated[float, Form()] = 20,
    target_service_level: Annotated[float, Form()] = 80,
    shrinkage: Annotated[float, Form()] = 30,
) -> dict:
    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / (file.filename or "cdr.csv")
            await _save_upload(file, path)
            clean = preprocess_cdr(path)
            intervals = build_interval_forecast(clean, interval_minutes)
            rows = []
            for row in intervals.itertuples(index=False):
                result = required_agents(
                    row.call_volume,
                    float(row.aht_seconds),
                    row.interval_seconds,
                    target_seconds,
                    target_service_level,
                    shrinkage,
                )
                rows.append({
                    "interval_start": row.call_datetime.isoformat(),
                    "call_volume": int(row.call_volume),
                    "aht_seconds": round(float(row.aht_seconds), 2),
                    "traffic_erlangs": round(result["traffic_erlangs"], 4),
                    "raw_agents": result["raw_agents"],
                    "scheduled_agents": result["scheduled_agents"],
                    "service_level_percent": round(result["service_level"] * 100, 2),
                    "probability_waiting_percent": round(result["probability_waiting"] * 100, 2),
                    "occupancy_percent": round(result["occupancy"] * 100, 2),
                    "asa_seconds": round(result["asa_seconds"], 2),
                })
            return {
                "filename": file.filename,
                "interval_minutes": interval_minutes,
                "valid_call_count": int(len(clean)),
                "interval_count": int(len(rows)),
                "forecast": rows,
            }
    except HTTPException:
        raise
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Forecast failed: {exc}") from exc


@app.post("/api/v1/cdr/multi-dataset-forecast")
async def multi_dataset_forecast(
    files: Annotated[list[UploadFile], File(description="Two or more full-year CDR CSV files")],
    interval_minutes: Annotated[int, Form()] = 30,
    target_seconds: Annotated[float, Form()] = 20,
    target_service_level: Annotated[float, Form()] = 80,
    shrinkage: Annotated[float, Form()] = 30,
    max_agents: Annotated[int, Form()] = 1000,
    include_forecast_rows: Annotated[bool, Form()] = True,
) -> dict:
    if len(files) < 2:
        raise HTTPException(status_code=400, detail="Upload at least two yearly CDR datasets.")

    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths: list[Path] = []
            filenames: list[str] = []
            for index, upload in enumerate(files, start=1):
                filename = upload.filename or f"dataset_{index}.csv"
                suffix = Path(filename).suffix or ".csv"
                path = Path(temp_dir) / f"dataset_{index}{suffix}"
                await _save_upload(upload, path)
                paths.append(path)
                filenames.append(filename)

            forecast, summary = build_multi_dataset_forecast(
                file_paths=paths,
                filenames=filenames,
                interval_minutes=interval_minutes,
                target_seconds=target_seconds,
                target_service_level=target_service_level,
                shrinkage=shrinkage,
                max_agents=max_agents,
            )
            response = {
                **summary,
                "parameters": {
                    "interval_minutes": interval_minutes,
                    "target_seconds": target_seconds,
                    "target_service_level_percent": target_service_level,
                    "shrinkage_percent": shrinkage,
                    "max_agents": max_agents,
                },
                "charts": build_dashboard_aggregates(forecast),
            }

            if include_forecast_rows:
                serializable = forecast.copy()
                serializable["interval_start"] = serializable["interval_start"].dt.strftime(
                    "%Y-%m-%dT%H:%M:%S"
                )
                response["forecast"] = serializable.to_dict(orient="records")
            else:
                response["forecast"] = []
            return response
    except HTTPException:
        raise
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Multi-dataset forecast failed: {exc}") from exc


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
    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths: list[Path] = []
            filenames: list[str] = []
            for index, upload in enumerate(files, start=1):
                filename = upload.filename or f"dataset_{index}.csv"
                suffix = Path(filename).suffix or ".csv"
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

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
