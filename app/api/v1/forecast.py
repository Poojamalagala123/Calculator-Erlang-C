from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from app.config import (
    MAX_UPLOAD_BYTES,
    MAX_UPLOAD_FILES,
    MAX_FORECAST_DAYS,
    MAX_AGENT_COUNT,
)
from app.core.forecasting.stl import build_stl_forecast
from app.core.forecasting.aggregates import build_dashboard_aggregates
from app.workers.task_manager import task_manager
from app.workers.jobs import run_stl_forecast_worker

router = APIRouter(prefix="/cdr", tags=["Demand Forecasting"])

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

@router.post("/stl-forecast")
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

@router.post("/stl-forecast/async")
async def stl_forecast_async(
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
    """
    Non-blocking Asynchronous Endpoint.
    Returns immediately with a job_id (<50ms). Status and live progress can be polled at /api/v1/jobs/{job_id}
    or streamed via SSE at /api/v1/jobs/{job_id}/stream.
    """
    if not files:
        raise HTTPException(status_code=400, detail="Upload at least one yearly CDR dataset.")
    if len(files) > MAX_UPLOAD_FILES:
        raise HTTPException(status_code=400, detail=f"Upload no more than {MAX_UPLOAD_FILES} files at a time.")
    if forecast_days < 1 or forecast_days > MAX_FORECAST_DAYS:
        raise HTTPException(status_code=400, detail=f"forecast_days must be between 1 and {MAX_FORECAST_DAYS}.")
    if max_agents < 1 or max_agents > MAX_AGENT_COUNT:
        raise HTTPException(status_code=400, detail=f"max_agents must be between 1 and {MAX_AGENT_COUNT}.")

    temp_dir_path = Path(tempfile.mkdtemp(prefix="cdr_upload_"))
    paths: list[Path] = []
    filenames: list[str] = []

    try:
        for index, upload in enumerate(files, start=1):
            filename = upload.filename or f"dataset_{index}.csv"
            suffix = Path(filename).suffix or ".csv"
            if suffix.lower() not in {".csv", ".txt"}:
                raise HTTPException(status_code=400, detail=f"Unsupported file type for {filename}.")
            path = temp_dir_path / f"dataset_{index}{suffix}"
            await _save_upload(upload, path)
            paths.append(path)
            filenames.append(filename)

        job_id = task_manager.create_job()

        # Submit task to background worker thread pool
        task_manager.submit_task(
            job_id,
            run_stl_forecast_worker,
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
            include_forecast_rows=include_forecast_rows,
            temp_dir=temp_dir_path,
        )

        return {
            "status": "queued",
            "job_id": job_id,
            "message": "Forecast processing started asynchronously.",
            "poll_url": f"/api/v1/jobs/{job_id}",
            "stream_url": f"/api/v1/jobs/{job_id}/stream",
        }
    except Exception:
        import shutil
        if temp_dir_path.exists():
            shutil.rmtree(temp_dir_path, ignore_errors=True)
        raise
