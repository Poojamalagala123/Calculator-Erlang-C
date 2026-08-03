"""Service routes for STL decomposition forecasting."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Annotated, Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from calculator import build_dashboard_aggregates
from stl_forecasting import build_stl_forecast

router = APIRouter()


async def _save_upload(upload: UploadFile, destination: Path) -> None:
    content = await upload.read()
    if not content:
        raise HTTPException(
            status_code=400,
            detail=f"{upload.filename or 'Uploaded file'} is empty.",
        )
    destination.write_bytes(content)


@router.post("/api/v1/cdr/stl-forecast")
async def stl_forecast(
    files: Annotated[
        list[UploadFile],
        File(description="Two or more consecutive yearly CDR CSV files"),
    ],
    interval_minutes: Annotated[int, Form()] = 30,
    seasonal_period: Annotated[Optional[int], Form()] = None,
    target_seconds: Annotated[float, Form()] = 20,
    target_service_level: Annotated[float, Form()] = 80,
    shrinkage: Annotated[float, Form()] = 30,
    max_agents: Annotated[int, Form()] = 1000,
    include_forecast_rows: Annotated[bool, Form()] = True,
) -> dict:
    if len(files) < 2:
        raise HTTPException(
            status_code=400,
            detail="Upload at least two yearly CDR datasets.",
        )

    try:
        with tempfile.TemporaryDirectory() as temporary_directory:
            paths: list[Path] = []
            filenames: list[str] = []
            for index, upload in enumerate(files, start=1):
                filename = upload.filename or f"dataset_{index}.csv"
                suffix = Path(filename).suffix or ".csv"
                path = Path(temporary_directory) / f"dataset_{index}{suffix}"
                await _save_upload(upload, path)
                paths.append(path)
                filenames.append(filename)

            forecast, summary = build_stl_forecast(
                file_paths=paths,
                filenames=filenames,
                interval_minutes=interval_minutes,
                seasonal_period=seasonal_period,
                target_seconds=target_seconds,
                target_service_level=target_service_level,
                shrinkage=shrinkage,
                max_agents=max_agents,
            )
            response = {
                **summary,
                "parameters": {
                    "interval_minutes": interval_minutes,
                    "seasonal_period": summary["seasonal_period"],
                    "target_seconds": target_seconds,
                    "target_service_level_percent": target_service_level,
                    "shrinkage_percent": shrinkage,
                    "max_agents": max_agents,
                },
                "charts": build_dashboard_aggregates(forecast),
            }

            if include_forecast_rows:
                serializable = forecast.copy()
                serializable["interval_start"] = serializable[
                    "interval_start"
                ].dt.strftime("%Y-%m-%dT%H:%M:%S")
                response["forecast"] = serializable.to_dict(orient="records")
            else:
                response["forecast"] = []
            return response
    except HTTPException:
        raise
    except (ValueError, RuntimeError) as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail=f"STL forecast failed: {error}",
        ) from error

