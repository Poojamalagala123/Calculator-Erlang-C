from __future__ import annotations
from pydantic import BaseModel, Field

class ForecastSummaryResponse(BaseModel):
    datasets_used: int
    historical_years: list[int]
    forecast_days: int
    predicted_calls: int
    average_aht_seconds: float
    max_scheduled_agents: int
    average_service_level_percent: float
    average_occupancy_percent: float
    seasonal_period: int
    charts: dict
    parameters: dict
    forecast: list[dict] = []
