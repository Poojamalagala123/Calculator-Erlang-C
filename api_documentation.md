# API Documentation — CDR STL Forecast & Agent Scheduling API

**Version:** `4.1.0`

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
| `POST` | `/api/v1/cdr/stl-forecast/async` | `multipart/form-data` | Submit background forecast job |
| `GET` | `/api/v1/jobs/{job_id}` | JSON response | Read job progress and completed result |
| `GET` | `/api/v1/jobs/{job_id}/stream` | `text/event-stream` response | Stream job progress |
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
  "version": "4.1.0"
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
| `files` | repeated file | — | yes | 1-20 non-empty `.csv`/`.txt` files; maximum 100 MiB per file |
| `interval_minutes` | integer | `30` | no | > 0 and exact divisor of 1440 |
| `forecast_days` | integer | `365` | no | 1-3650; `365` expands to 366 days for a leap output year |
| `seasonal_period` | integer or omitted | automatic | no | Effective cycle >= 2 intervals; omitted or `0` uses weekly seasonality |
| `trend_lookback_days` | integer | `90` | no | >= 7 |
| `target_seconds` | number | `20` | no | >= 0 |
| `target_service_level` | number | `80` | no | after percent conversion, strictly between 0 and 1 |
| `shrinkage` | number | `30` | no | after percent conversion, >= 0 and < 1 |
| `max_agents` | integer | `1000` | no | 1-10,000; neither raw nor scheduled staffing may exceed it |
| `include_forecast_rows` | boolean | `true` | no | controls whether full forecast rows are returned |

Both `80` and `0.80` are accepted as an 80% service-level target. Likewise `30` and `0.30` represent 30% shrinkage. Values greater than 1 are divided by 100; values at or below 1 are interpreted as fractions. Exactly `1` means 100% and is rejected for both settings. Send numeric form values without a `%` suffix.

Omit `seasonal_period` for automatic weekly seasonality; the current implementation also treats `0` as automatic. Nonzero values must be at least 2, and the historical series must contain at least two complete cycles. The effective trend lookback is capped at the available historical trend.

### Dashboard request settings

The dashboard exposes only file selection, target answer seconds (default `20`), and target service level (default `80`). It sends the following additional fields to the async endpoint:

| Field | Dashboard value |
|---|---|
| `interval_minutes` | `30` |
| `forecast_days` | `365` |
| `seasonal_period` | `336` |
| `trend_lookback_days` | `90` |
| `shrinkage` | `30` |
| `include_forecast_rows` | `true` |
| `max_agents` | Omitted; API default `1000` applies |

The table's display interval defaults to 1 hour and can be changed to 0.5, 1, 2, 4, or 8 hours. This groups returned rows; it does not change the forecast request or rerun Erlang C.

## Forecast dates and leap years

The forecast starts on January 1 of `output_year`, which is the latest historical year plus one. A request with `forecast_days=365` produces a complete output year, including February 29 in leap years. Other horizon values retain their requested number of consecutive calendar days.

For a leap output year, an annual request returns `days: 366` and, at `interval_minutes=30`, `forecast_interval_count: 17568`. `parameters.forecast_days` still records the original request value (`365`). The historical preprocessing step that removes February 29 applies only to historical inputs; future forecast dates include leap day.

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

At least one valid answered record must remain in every uploaded file. Full-year input is expected, but coverage of every month is not validated: missing intervals are filled with zero calls. Historical years need not be consecutive.

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

Example values above are illustrative; actual values come from the uploaded CDR data. Summary AHT uses weights `max(call_volume, 1)`. Summary service level, occupancy, and ASA are arithmetic means over intervals. `parameters.target_service_level_percent` and `parameters.shrinkage_percent` echo the original request values, so fractional inputs remain fractional in those fields.

`day_of_year` is the calendar day-of-year, and `weekday` uses 0 for Monday through 6 for Sunday. Timestamps have no timezone suffix.

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

# POST `/api/v1/cdr/stl-forecast/async`

Accepts the same multipart fields as the synchronous forecast endpoint. After saving the uploads, it submits a background job and returns HTTP 200:

```json
{
  "status": "queued",
  "job_id": "example-job-id",
  "message": "Forecast processing started asynchronously.",
  "poll_url": "/api/v1/jobs/example-job-id",
  "stream_url": "/api/v1/jobs/example-job-id/stream"
}
```

The submission response does not contain the forecast. Processing failures are reported in the job's `error` field with `status: failed`. Upload and request errors can occur before a job is created.

# GET `/api/v1/jobs/{job_id}`

Returns HTTP 200 with job status and the forecast result when complete:

```json
{
  "job_id": "example-job-id",
  "status": "processing",
  "progress": 45,
  "message": "Processing forecast...",
  "error": null,
  "result": null
}
```

Statuses are `queued`, `processing`, `completed`, or `failed`. Progress ranges from 0 to 100. When completed, `result` has the same forecast response structure as the synchronous endpoint. Read `result.forecast` for later scheduling requests. Unknown job IDs return HTTP 404.

# GET `/api/v1/jobs/{job_id}/stream`

Streams server-sent events as `text/event-stream` when progress or status changes. Each `data:` JSON object contains `job_id`, `status`, `progress`, `message`, `error`, and `has_result`. The stream closes after `completed` or `failed`. Fetch the polling endpoint to retrieve the completed forecast; the stream does not contain the full result. Unknown job IDs return HTTP 404.

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
| `forecast` | array | 1-40,000 rows |
| `year` | integer | Required |
| `month` | integer | 1 to 12 |
| `agent_count` | integer/null | 1-10,000 when supplied; must be >= minimum estimated headcount |

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

The final minimum employee count is the maximum required value across the month. Null/omitted `agent_count` uses this estimate. If the estimate is zero, automatic generation fails the positive-agent-count check.

The estimate does not guarantee full coverage under the greedy assignment and rest constraints. HTTP 200 can contain `summary.coverage_ok: false`; inspect `coverage_shortage` and the per-shift `coverage` rows. Months are generated independently, without prior-month assignments for boundary rest or weekly-limit checks.

## Assignment rules

- no more than one shift per agent per day;
- no more than five working days per Monday-Sunday week;
- at least eight hours between successive working shifts within the generated month;
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

- forecast must contain 1-40,000 rows;
- schedule must contain 1-50,000 rows;
- forecast must include `interval_start` and `scheduled_agents` to rebuild shift requirements;
- `agent_id` must be 1-100 characters; `leave_date` must be 1-10 characters and parse as a date (use `YYYY-MM-DD`);
- schedule must include `agent_id`, `date`, `shift_code`, `shift`, `status`;
- exactly one matching schedule row must exist for the selected agent/date.

## Optional replacement fields

| Field | Type | Default | Meaning |
|---|---|---|---|
| `replacement_agent_id` | string/null | `null` | Selected replacement; 1-100 characters when supplied |
| `auto_assign` | boolean | `true` | Set to `false` to require manual selection when coverage is needed |

Add these fields to the leave request for dashboard-style manual coverage:

```json
{
  "replacement_agent_id": "Agent 007",
  "auto_assign": false
}
```

This fragment supplements the required forecast, schedule, agent, and date fields. Invalid or missing manual selections return HTTP 400 when a replacement is needed. No alternative agent is automatically assigned.

## Automatic resolution logic

1. Mark leave and calculate coverage. No new leave action returns `NO_ACTION`; sufficient remaining coverage returns `NO_REPLACEMENT_REQUIRED`.
2. Rank eligible OFF candidates and attempt the first candidate, including rest validation. Successful coverage returns `OFF_AGENT_COVER`.
3. If coverage remains unresolved, rank eligible transfers from other shifts and attempt the first candidate with rest validation. Successful coverage returns `SHIFT_TRANSFER`.
4. If coverage remains insufficient, return `UNRESOLVED` with `resolved: false` and the updated schedule.

A chosen candidate failing rest validation raises HTTP 400 immediately. Automatic mode does not exhaustively retry all candidates or necessarily reach the transfer step after a failed OFF-agent rest check.

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
original_shift_code
original_shift
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

- schedule is empty or exceeds 50,000 rows;
- either agent ID is outside 1-100 characters, or `swap_date` is outside 1-10 characters (use `YYYY-MM-DD`);
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

The minimum rest period is currently 8 hours for automatic leave coverage and shift swaps. Manually selected leave replacements skip rest validation, while retaining weekly work-limit and source-coverage eligibility checks.

For a modified working shift, the validator compares it with the agent's working assignment on the previous and next calendar date present in the supplied schedule. Missing adjacent dates are not checked.

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

# Request limits and examples

Limits are defined in `app/config.py` and the request models in `app/schemas/schedule.py`:

| Input | Limit |
|---|---|
| Forecast uploads | 20 files, 100 MiB per file |
| Requested forecast horizon | 1-3650 days |
| `max_agents` / supplied monthly `agent_count` | 1-10,000 |
| Forecast rows in monthly/leave JSON requests | 1-40,000 |
| Schedule rows in leave/swap JSON requests | 1-50,000 |
| Agent ID fields | 1-100 characters |
| Leave/swap date fields | 1-10 characters; use `YYYY-MM-DD` |

These array limits apply to incoming scheduling requests; generating a large forecast or roster does not guarantee it fits a subsequent request. Supply the relevant month's source forecast rows when needed. The CSV's aggregated display columns do not replace the original API fields.

Request examples using `[...]` are schematic: replace each placeholder with the actual returned forecast or schedule rows before sending JSON. Preserve the latest schedule after leave or swap operations.

# HTTP error behaviour

## 400 Bad Request

Used for expected validation/data failures.

Shape:

```json
{
  "detail": "Human-readable reason"
}
```

## 404 Not Found

Returned when a polling or streaming request uses an unknown job ID. Jobs are lost when the server process restarts.

## 413 Content Too Large

Returned when an uploaded file exceeds `100 * 1024 * 1024` bytes (100 MiB).

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

# Dashboard filtering and CSV exports

CSV downloads are assembled in the browser. The forecast and scheduling endpoints return JSON; there is no dedicated CSV or yearly-roster endpoint.

## Forecast CSV

**Download Forecast CSV** produces `stl_erlang_forecast_<year>_hourly.csv` using all available forecast rows in the output year. It ignores the dashboard's selected date and display interval and groups source rows into 60-minute wall-clock intervals.

| CSV column (in order) | Source / calculation |
|---|---|
| `Interval` | Hour range, such as `2026-01-01 00:00 - 01:00` |
| `Calls` | Sum of `call_volume` |
| `AHT` | Call-weighted `aht_seconds`, formatted to two decimals |
| `Raw agents` | Maximum `raw_agents` within the hour |
| `Scheduled agents` | Maximum `scheduled_agents` within the hour |
| `Service level %` | Call-weighted `service_level_percent`, formatted to two decimals |

For zero-call hours, AHT and service level use arithmetic averages of the source intervals. These calculations match the dashboard table. The last hourly label ends at `24:00`. A full-year export contains 8,760 data rows, or 8,784 in a leap year. Forecasts created before leap-day support must be regenerated to include February 29.

## Roster week filter

The table defaults to **Whole month**. **Week 1**, **Week 2**, etc. represent Monday-Sunday calendar weeks, clipped to the selected month. A month starting on Wednesday shows Wednesday-Sunday in its first week; months can have four, five, or six displayed weeks. Filtering changes the visible day columns and `Total shifts`, without modifying API schedule rows. Generating a new roster resets the filter; leave and swap updates preserve it.

## Yearly roster CSV

**Download Yearly Roster CSV** produces `agent_roster_<year>.csv` with 12 January-December sections separated by blank rows. Each section contains:

1. A title such as `Agent Monthly Roster - April 2026` (month names follow browser locale).
2. A header: `Agent`, each calendar day formatted like `01 Wed`, then `Total shifts`.
3. One row per agent, containing the dashboard's shift labels or `OFF`/`LEAVE`, followed by the number of `WORK` assignments in that month.

The export always uses full months, regardless of the selected week. Cached monthly rosters retain leave and swap edits. For missing months the browser calls `/api/v1/schedule/monthly` sequentially with that month's forecast rows, the output year, the month, and the agent-count setting from the most recent successful dashboard generation. `agent_count: null` calculates minimum staffing independently for each missing month. Cached months retain their existing staffing and edits.

Missing forecast data, insufficient headcount, or another monthly-generation error stops the export without saving a partial CSV. The dashboard displays progress and the error. Successful monthly results remain cached for retries during the same forecast session. A monthly response with `coverage_ok: false` is not treated as an export error; the yearly CSV combines independent monthly rosters without annual boundary validation.

Both CSV files use UTF-8 with BOM and CRLF row endings. Fields containing commas, quotes, or newlines are CSV-escaped. CSV preserves row/column layout but cannot store dashboard colours, styles, or separate spreadsheet worksheets.

---

# State handling

The API does not store forecast or schedule state in a database. Async job status and results live in the server process's memory and disappear on restart. They are not shared between separate server processes.

The dashboard keeps forecasts and monthly rosters in browser memory. Starting a new forecast clears the monthly roster cache; reloading the page loses browser session state. Yearly CSV export reuses the cached monthly rosters for the current forecast.

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
