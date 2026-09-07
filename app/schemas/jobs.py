from __future__ import annotations
from typing import Any, Optional
from pydantic import BaseModel

class JobStatusResponse(BaseModel):
    job_id: str
    status: str
    progress: int = 0
    message: str = ""
    error: Optional[str] = None
    result: Optional[dict[str, Any]] = None
