from __future__ import annotations

from pathlib import Path
import pandas as pd
from app.core.constants import CDR_COLUMNS

def read_cdr_csv(file_path: str | Path) -> pd.DataFrame:
    attempts = [
        {"encoding": "utf-16", "sep": "\t"},
        {"encoding": "utf-8-sig", "sep": ","},
        {"encoding": "latin1", "sep": ","},
    ]
    last_error: Exception | None = None
    for kwargs in attempts:
        try:
            # Try high-speed C engine first; fallback to python engine if needed
            try:
                frame = pd.read_csv(
                    file_path,
                    header=None,
                    names=CDR_COLUMNS,
                    engine="c",
                    dtype=str,
                    **kwargs,
                )
            except Exception:
                frame = pd.read_csv(
                    file_path,
                    header=None,
                    names=CDR_COLUMNS,
                    engine="python",
                    dtype=str,
                    **kwargs,
                )
            if frame[CDR_COLUMNS[1:]].isna().all().all():
                raise ValueError("The selected separator did not produce seven columns.")
            return frame
        except Exception as exc:
            last_error = exc
    raise ValueError(f"Could not read CDR CSV. Last error: {last_error}")
