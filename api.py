from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Annotated

import pandas as pd
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

from calculator import (
    average_speed_of_answer,
    build_interval_forecast,
    calculate_aht,
    calculate_traffic,
    erlang_c_probability,
    occupancy,
    preprocess_cdr,
    required_agents,
    service_level,
)


app = FastAPI(
    title="Erlang C Calculator API",
    description="REST API for call-centre Erlang C calculations and CDR processing.",
    version="1.0.0",
)


# -------------------------------------------------------------------
# Request models
# -------------------------------------------------------------------

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

    # Both 80 and 0.80 are accepted by your clean_percent function.
    target_service_level: float = Field(gt=0)

    # Both 30 and 0.30 are accepted.
    shrinkage: float = Field(default=0, ge=0)

    max_agents: int = Field(default=1000, gt=0)


# -------------------------------------------------------------------
# General endpoints
# -------------------------------------------------------------------

@app.get("/")
def root() -> dict:
    return {
        "message": "Erlang C Calculator API",
        "documentation": "/docs",
        "health": "/health",
    }


@app.get("/health")
def health() -> dict:
    return {"status": "healthy"}


# -------------------------------------------------------------------
# Calculator endpoints
# -------------------------------------------------------------------

@app.post("/api/v1/aht")
def get_aht(request: AHTRequest) -> dict:
    try:
        result = calculate_aht(
            total_handle_time_seconds=request.total_handle_time_seconds,
            total_answered_calls=request.total_answered_calls,
        )

        return {
            "aht_seconds": round(result, 2),
        }

    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/v1/traffic")
def get_traffic(request: TrafficRequest) -> dict:
    try:
        result = calculate_traffic(
            call_volume=request.call_volume,
            aht_seconds=request.aht_seconds,
            interval_seconds=request.interval_seconds,
        )

        return {
            "traffic_erlangs": round(result, 4),
        }

    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/v1/erlang-outputs")
def get_erlang_outputs(request: ErlangOutputsRequest) -> dict:
    try:
        probability_waiting = erlang_c_probability(
            traffic=request.traffic_erlangs,
            agents=request.agents,
        )

        asa_seconds = average_speed_of_answer(
            erlang_c=probability_waiting,
            aht_seconds=request.aht_seconds,
            agents=request.agents,
            traffic=request.traffic_erlangs,
        )

        calculated_service_level = service_level(
            erlang_c=probability_waiting,
            agents=request.agents,
            traffic=request.traffic_erlangs,
            target_seconds=request.target_seconds,
            aht_seconds=request.aht_seconds,
        )

        calculated_occupancy = occupancy(
            traffic=request.traffic_erlangs,
            agents=request.agents,
        )

        return {
            "traffic_erlangs": request.traffic_erlangs,
            "agents": request.agents,
            "probability_waiting": round(probability_waiting, 6),
            "probability_waiting_percent": round(
                probability_waiting * 100,
                2,
            ),
            "asa_seconds": (
                None if asa_seconds == float("inf")
                else round(asa_seconds, 2)
            ),
            "service_level": round(calculated_service_level, 6),
            "service_level_percent": round(
                calculated_service_level * 100,
                2,
            ),
            "occupancy": round(calculated_occupancy, 6),
            "occupancy_percent": round(
                calculated_occupancy * 100,
                2,
            ),
        }

    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Calculation failed: {exc}",
        ) from exc


@app.post("/api/v1/required-agents")
def get_required_agents(request: RequiredAgentsRequest) -> dict:
    try:
        result = required_agents(
            call_volume=request.call_volume,
            aht_seconds=request.aht_seconds,
            interval_seconds=request.interval_seconds,
            target_seconds=request.target_seconds,
            target_service_level=request.target_service_level,
            shrinkage=request.shrinkage,
            max_agents=request.max_agents,
        )

        return {
            "traffic_erlangs": round(result["traffic_erlangs"], 4),
            "raw_agents": result["raw_agents"],
            "scheduled_agents": result["scheduled_agents"],
            "service_level": round(result["service_level"], 6),
            "service_level_percent": round(
                result["service_level"] * 100,
                2,
            ),
            "probability_waiting": round(
                result["probability_waiting"],
                6,
            ),
            "probability_waiting_percent": round(
                result["probability_waiting"] * 100,
                2,
            ),
            "occupancy": round(result["occupancy"], 6),
            "occupancy_percent": round(
                result["occupancy"] * 100,
                2,
            ),
            "asa_seconds": round(result["asa_seconds"], 2),
        }

    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Calculation failed: {exc}",
        ) from exc


# -------------------------------------------------------------------
# CDR file endpoint
# -------------------------------------------------------------------

@app.post("/api/v1/cdr/forecast")
async def forecast_from_cdr(
    file: Annotated[UploadFile, File(description="CDR CSV file")],
    interval_minutes: Annotated[int, Form()] = 60,
    target_seconds: Annotated[float, Form()] = 20,
    target_service_level: Annotated[float, Form()] = 80,
    shrinkage: Annotated[float, Form()] = 30,
) -> dict:
    if interval_minutes <= 0:
        raise HTTPException(
            status_code=400,
            detail="interval_minutes must be greater than 0.",
        )

    file_extension = Path(file.filename or "cdr.csv").suffix or ".csv"

    try:
        file_content = await file.read()

        if not file_content:
            raise HTTPException(
                status_code=400,
                detail="The uploaded file is empty.",
            )

        with NamedTemporaryFile(
            mode="wb",
            suffix=file_extension,
            delete=False,
        ) as temporary_file:
            temporary_file.write(file_content)
            temporary_path = temporary_file.name

        clean_df = preprocess_cdr(temporary_path)

        if clean_df.empty:
            raise HTTPException(
                status_code=400,
                detail="No valid answered calls were found in the file.",
            )

        interval_df = build_interval_forecast(
            clean_df,
            interval_minutes=interval_minutes,
        )

        forecast = []

        for _, row in interval_df.iterrows():
            result = required_agents(
                call_volume=float(row["call_volume"]),
                aht_seconds=float(row["aht_seconds"]),
                interval_seconds=float(row["interval_seconds"]),
                target_seconds=target_seconds,
                target_service_level=target_service_level,
                shrinkage=shrinkage,
            )

            forecast.append(
                {
                    "interval_start": row["call_datetime"].isoformat(),
                    "call_volume": int(row["call_volume"]),
                    "aht_seconds": round(
                        float(row["aht_seconds"]),
                        2,
                    ),
                    "traffic_erlangs": round(
                        result["traffic_erlangs"],
                        4,
                    ),
                    "raw_agents": result["raw_agents"],
                    "scheduled_agents": result["scheduled_agents"],
                    "service_level_percent": round(
                        result["service_level"] * 100,
                        2,
                    ),
                    "probability_waiting_percent": round(
                        result["probability_waiting"] * 100,
                        2,
                    ),
                    "occupancy_percent": round(
                        result["occupancy"] * 100,
                        2,
                    ),
                    "asa_seconds": round(
                        result["asa_seconds"],
                        2,
                    ),
                }
            )

        return {
            "filename": file.filename,
            "interval_minutes": interval_minutes,
            "valid_call_count": len(clean_df),
            "interval_count": len(forecast),
            "forecast": forecast,
        }

    except HTTPException:
        raise
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"CDR processing failed: {exc}",
        ) from exc
    finally:
        if "temporary_path" in locals():
            Path(temporary_path).unlink(missing_ok=True)