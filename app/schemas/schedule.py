from __future__ import annotations
from pydantic import BaseModel, Field
from app.config import MAX_FORECAST_ROWS, MAX_SCHEDULE_ROWS, MAX_AGENT_COUNT

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
    replacement_agent_id: str | None = Field(default=None, min_length=1, max_length=100)
    auto_assign: bool = True

class AgentShiftSwapRequest(BaseModel):
    schedule: list[dict] = Field(min_length=1, max_length=MAX_SCHEDULE_ROWS)
    agent_1: str = Field(min_length=1, max_length=100)
    agent_2: str = Field(min_length=1, max_length=100)
    swap_date: str = Field(min_length=1, max_length=10)
