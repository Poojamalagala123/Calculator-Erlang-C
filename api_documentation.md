# API Documentation — CDR STL Forecast & Agent Scheduling API

**Version:** `4.0.0`  
**Default base URL:** `http://127.0.0.1:8000`

Interactive documentation:

- Swagger UI: `/docs`
- ReDoc: `/redoc`
- OpenAPI schema: `/openapi.json`

## Endpoint summary

| Method | Path | Content type | Description |
|---|---|---|---|
| `GET` | `/` | — | Serve dashboard or return service information |
| `GET` | `/health` | — | Return health and version |
| `POST` | `/api/v1/cdr/stl-forecast` | `multipart/form-data` | Forecast calls and Erlang C staffing using STL |
| `POST` | `/api/v1/schedule/monthly` | `application/json` | Generate monthly agent roster |
| `POST` | `/api/v1/schedule/leave` | `application/json` | Apply leave and attempt safe coverage resolution |
| `POST` | `/api/v1/schedule/swap` | `application/json` | Swap two working agents' shifts |

---

## GET `/`

If `static/index.html` exists, the API returns that dashboard file.

Otherwise the fallback response is service information similar to:

```json
{
  "message": "CDR STL Forecast & Agent Scheduling API",
  "documentation": "/docs"
}
```

---

## GET `/health`

### Response

```json
{
  "status": "healthy",
  "version": "4.0.0"
}
```

---

# POST `/api/v1/cdr/stl-forecast`

Forecast future contact volume with STL decomposition and calculate Erlang C staffing for every future interval.

## Content type

```text
multipart/form-data
```

## Form fields

| Field | Type | Default | Required | Validation |
|---|---|---:|---|---|
| `files` | repeated file | — | yes | At least one non-empty CDR file |
| `interval_minutes` | integer | `30` | no | > 0 and exact divisor of 1440 |
| `forecast_days` | integer | `365` | no | > 0 |
| `seasonal_period` | integer/null | automatic | no | >= 2 when provided |
| `trend_lookback_days` | integer | `90` | no | >= 7 |
| `target_seconds` | number | `20` | no | >= 0 |
| `target_service_level` | number | `80` | no | after percent conversion, strictly between 0 and 1 |
| `shrinkage` | number | `30` | no | after percent conversion, >= 0 and < 1 |
| `max_agents` | integer | `1000` | no | > 0 |
| `include_forecast_rows` | boolean | `true` | no | controls whether full forecast rows are returned |

Both `80` and `0.80` are accepted as an 80% service-level target. Likewise `30` and `0.30` represent 30% shrinkage.

## CDR file schema

Seven columns, no header expected:

```text
source,destination,call_datetime,duration,disposition,unique_id,caller_id
```

Accepted read attempts:

```text
UTF-16 + tab
UTF-8-SIG + comma
Latin-1 + comma
```

Expected call timestamp format:

```text
%Y-%b-%d %I:%M:%S %p
```

Example:

```text
2024-Mar-12 03:25:19 PM
```

Duration must be `HH:MM:SS`.

## CDR cleaning

A row is retained only when:

- timestamp parses;
- duration parses;
- source is non-empty;
- destination is non-empty;
- destination is not `s` case-insensitively;
- disposition is `Answered` by default;
- duration is between 1 second and 4 hours by default.

## Dataset validation

Each file must contain exactly one calendar year after cleaning.

Duplicate years across uploaded files are rejected.

At least one valid answered record must remain in every uploaded file.

## Forecast processing

For every dataset:

```text
read file
→ clean CDR
→ identify unique year
→ build complete fixed interval grid
→ remove Feb 29
```

All yearly interval frames are concatenated chronologically.

STL requires at least two complete seasonal periods of historical data.

### Default seasonality

```text
intervals_per_day = 1440 / interval_minutes
seasonal_period = intervals_per_day * 7
```

At 30 minutes:

```text
seasonal_period = 336
```

### Forecast equation

Conceptually:

```text
future_calls = extrapolated_STL_trend + repeated_STL_seasonality
```

The result is rounded and clipped to zero or above.

Residual noise is not extrapolated.

### Future AHT

Future AHT is calculated from historical weighted AHT by weekly time slot. Missing slots fall back to global weighted historical AHT.

### Erlang C

For every future interval:

```text
traffic = calls * AHT / interval_seconds
```

`pyworkforce.queuing.ErlangC.required_positions()` calculates raw and shrinkage-adjusted positions.

## Example cURL

```bash
curl -X POST "http://127.0.0.1:8000/api/v1/cdr/stl-forecast" \
  -F "files=@calls_2023.csv" \
  -F "files=@calls_2024.csv" \
  -F "interval_minutes=30" \
  -F "forecast_days=365" \
  -F "trend_lookback_days=90" \
  -F "target_seconds=20" \
  -F "target_service_level=80" \
  -F "shrinkage=30" \
  -F "max_agents=1000" \
  -F "include_forecast_rows=true"
```

## Response structure

Representative shape:

```json
{
  "method": "STL",
  "logic": "STL weekly decomposition with linear trend extrapolation and repeated seasonal cycle.",
  "dataset_count": 2,
  "historical_years": [2023, 2024],
  "output_year": 2025,
  "days": 365,
  "interval_minutes": 30,
  "seasonal_period": 336,
  "trend_lookback_days": 90,
  "forecast_interval_count": 17520,
  "total_predicted_calls": 123456,
  "average_aht_seconds": 180.5,
  "maximum_raw_agents": 30,
  "maximum_scheduled_agents": 43,
  "average_service_level_percent": 81.2,
  "average_occupancy_percent": 72.4,
  "average_asa_seconds": 14.1,
  "intervals_below_service_target": 10,
  "peak_interval": {
    "interval_start": "2025-07-01T10:30:00",
    "call_volume": 95,
    "scheduled_agents": 43
  },
  "source_data": {
    "dataset_count": 2,
    "historical_years": [2023, 2024],
    "datasets": []
  },
  "decomposition_summary": {
    "historical_intervals": 35040,
    "trend_last_value": 10.1,
    "trend_slope_per_interval": 0.00001234,
    "seasonal_min": -5.2,
    "seasonal_max": 7.8,
    "residual_std": 2.4
  },
  "parameters": {
    "interval_minutes": 30,
    "forecast_days": 365,
    "seasonal_period": 336,
    "trend_lookback_days": 90,
    "target_seconds": 20,
    "target_service_level_percent": 80,
    "shrinkage_percent": 30,
    "max_agents": 1000
  },
  "charts": {
    "monthly": [],
    "daily": [],
    "weekday": [],
    "time_of_day": []
  },
  "forecast": []
}
```

Example values above are illustrative; actual values come from the uploaded CDR data.

## Forecast row

```json
{
  "interval_start": "2025-01-01T00:00:00",
  "date": "2025-01-01",
  "day_of_year": 1,
  "month": 1,
  "day": 1,
  "weekday": 2,
  "weekday_name": "Wednesday",
  "hour": 0,
  "minute": 0,
  "call_volume": 10,
  "aht_seconds": 180.0,
  "traffic_erlangs": 1.0,
  "raw_agents": 3,
  "scheduled_agents": 5,
  "service_level_percent": 80.0,
  "probability_waiting_percent": 20.0,
  "occupancy_percent": 60.0,
  "asa_seconds": 10.0
}
```

If `include_forecast_rows=false`, `forecast` is returned as `[]` while summaries and charts are still generated.

## Error responses

User/data errors return status `400`, for example:

```json
{
  "detail": "Upload at least one yearly CDR dataset."
}
```

Unexpected internal forecast errors return status `500` with:

```json
{
  "detail": "STL forecast failed: ..."
}
```

---

# POST `/api/v1/schedule/monthly`

Generate a monthly schedule from forecast staffing requirements.

## Request model

```json
{
  "forecast": [...],
  "year": 2025,
  "month": 1,
  "agent_count": null
}
```

| Field | Type | Validation |
|---|---|---|
| `forecast` | array | Cannot be empty |
| `year` | integer | Required |
| `month` | integer | 1 to 12 |
| `agent_count` | integer/null | > 0 when supplied; must be >= minimum estimated headcount |

The forecast must contain at least:

```text
interval_start
scheduled_agents
```

## Shift model

```text
NIGHT   00:00-08:00
MORNING 08:00-16:00
EVENING 16:00-00:00
```

Required agents for each shift equal the maximum `scheduled_agents` value inside that shift.

## Headcount model

For each Monday-Sunday week:

```text
weekly capacity headcount = ceil(total required shift slots / 5)
```

The calculation also considers the maximum total agents required on any one day.

The final minimum employee count is the maximum required value across the month.

## Assignment rules

- no more than one shift per agent per day;
- no more than five working days per Monday-Sunday week;
- unassigned dates become `OFF`;
- assignments are balanced using existing weekly workload, total assignments, shift-specific assignments, and agent ID.

## Response

```json
{
  "summary": {
    "year": 2025,
    "month": 1,
    "minimum_agents": 30,
    "agent_count": 30,
    "shift_hours": 8,
    "working_days_per_week": 5,
    "days_off_per_week": 2,
    "total_required_shift_assignments": 600,
    "total_assigned_shift_assignments": 600,
    "coverage_shortage": 0,
    "coverage_ok": true,
    "coverage": []
  },
  "schedule": []
}
```

Working schedule row:

```json
{
  "agent_id": "Agent 001",
  "date": "2025-01-01",
  "weekday": "Wednesday",
  "shift_code": "MORNING",
  "shift": "08:00-16:00",
  "status": "WORK"
}
```

OFF schedule row:

```json
{
  "agent_id": "Agent 001",
  "date": "2025-01-02",
  "weekday": "Thursday",
  "shift_code": "OFF",
  "shift": "OFF",
  "status": "OFF"
}
```

---

# POST `/api/v1/schedule/leave`

Apply leave with a manually selected replacement or automatic coverage.

The dashboard sends `auto_assign: false` and `replacement_agent_id` (the selected agent ID). If coverage is needed, manual mode requires an eligible selection and never falls back to another agent. OFF agents must remain within the weekly work limit; transfers must preserve source-shift coverage. Minimum-rest validation is skipped for manual replacements.

Both fields are optional for existing API clients: `auto_assign` defaults to `true`, and omitting the replacement ID retains automatic coverage with rest validation. Supplying a replacement ID always selects manual mode. If staffing is already sufficient, no replacement is assigned.

## Request model

```json
{
  "forecast": [...],
  "schedule": [...],
  "agent_id": "Agent 004",
  "leave_date": "2025-01-12"
}
```

## Validation

- forecast cannot be empty;
- schedule cannot be empty;
- forecast must include `interval_start`;
- leave date must parse as a date;
- schedule must include `agent_id`, `date`, `shift_code`, `shift`, `status`;
- exactly one matching schedule row must exist for the selected agent/date.

## Resolution logic

```text
mark agent LEAVE
      ↓
check original shift coverage
      ↓
coverage sufficient? ── yes → NO_REPLACEMENT_REQUIRED
      ↓ no
find eligible OFF agent
      ↓
available + safe? ── yes → OFF_AGENT_COVER
      ↓ no
find safe agent from another shift with spare coverage
      ↓
available + safe? ── yes → SHIFT_TRANSFER
      ↓ no
UNRESOLVED
```

If the selected agent is already OFF or leave does not need to be applied, the method can be `NO_ACTION`.

### OFF-agent candidate rules

Candidate must:

- be OFF on the leave date;
- not be the leave agent;
- stay at or below five working days in that Monday-Sunday week after covering.

Ranking:

```text
fewest weekly workdays
→ fewest monthly workdays
→ agent ID
```

### Safe shift-transfer rules

The source shift must remain at or above required staffing after the candidate is moved.

Ranking:

```text
most spare agents in source shift
→ fewest monthly workdays
→ agent ID
```

### Audit values

The updated schedule may contain:

```text
previous_shift_code
previous_shift
assignment_type
```

Assignment types include:

```text
LEAVE_COVER
SHIFT_TRANSFER
```

## Response

```json
{
  "result": {
    "resolved": true,
    "method": "OFF_AGENT_COVER",
    "leave": {},
    "coverage_before": {},
    "coverage_after": {},
    "replacement": {},
    "message": "..."
  },
  "schedule": []
}
```

Possible methods:

```text
NO_ACTION
NO_REPLACEMENT_REQUIRED
OFF_AGENT_COVER
SHIFT_TRANSFER
UNRESOLVED
```

---

# POST `/api/v1/schedule/swap`

Swap the shifts of two agents on the same date.

## Request model

```json
{
  "schedule": [...],
  "agent_1": "Agent 001",
  "agent_2": "Agent 002",
  "swap_date": "2025-01-18"
}
```

## Validation

The request fails when:

- schedule is empty;
- both agent IDs are the same;
- one agent has no row on the date;
- one agent has multiple rows on the date;
- one agent is not `WORK`;
- one agent is `OFF` or `LEAVE`;
- both agents already have the same shift;
- either post-swap assignment violates the minimum rest rule.

## Processing

The API stores both original shift assignments, swaps `shift_code` and `shift`, and writes:

```text
assignment_type = SHIFT_SWAP
```

Both agents are then independently rest-validated.

## Response

```json
{
  "result": {
    "swap_applied": true,
    "date": "2025-01-18",
    "agent_1": "Agent 001",
    "agent_1_previous_shift": "MORNING",
    "agent_1_new_shift": "EVENING",
    "agent_2": "Agent 002",
    "agent_2_previous_shift": "EVENING",
    "agent_2_new_shift": "MORNING",
    "agent_1_rest_validation": {},
    "agent_2_rest_validation": {},
    "message": "..."
  },
  "schedule": []
}
```

---

# Rest-rule behaviour

The minimum rest period is currently 8 hours.

For a modified working shift, the scheduler compares it with the agent's working assignment on the previous and next calendar date.

A request fails when:

```text
new_shift_start - previous_shift_end < 8 hours
```

or:

```text
next_shift_start - new_shift_end < 8 hours
```

Shift times:

```text
NIGHT   00:00-08:00
MORNING 08:00-16:00
EVENING 16:00-24:00
```

---

# HTTP error behaviour

## 400 Bad Request

Used for expected validation/data failures.

Shape:

```json
{
  "detail": "Human-readable reason"
}
```

## 422 Unprocessable Entity

FastAPI/Pydantic can return `422` before endpoint logic runs when the JSON/request field type violates the declared request model, for example a monthly schedule `month` outside its Pydantic bounds.

## 500 Internal Server Error

Used for unexpected exceptions. Messages are prefixed by operation, for example:

```text
STL forecast failed: ...
Monthly schedule generation failed: ...
Agent leave processing failed: ...
Shift swap processing failed: ...
```

---

# State handling

The API does not store forecast or schedule state in a database.

The calling frontend/client is expected to pass the current forecast and/or schedule into later endpoints:

```text
STL response.forecast
        ↓
monthly schedule request.forecast
        ↓
monthly response.schedule
        ↓
leave request.schedule / swap request.schedule
```

Always use the latest returned schedule after a leave or swap operation so later requests operate on the current roster state.
