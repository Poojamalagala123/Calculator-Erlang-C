from __future__ import annotations
from typing import Any, Optional
from pydantic import BaseModel

class JobStatusResponse(BaseModel):
    job_id: str
    status: str  # "queued", "processing", "completed", "failed"
    progress: int = 0  # 0 to 100
    message: str = ""
    error: Optional[str] = None
    result: Optional[dict[str, Any]] = None
