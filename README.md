# CDR STL Forecast & Agent Scheduling API

FastAPI application for contact-centre demand forecasting and workforce scheduling.

The application uses historical CDR (Call Detail Record) files to forecast future call demand with STL decomposition, calculates interval-level Erlang C staffing requirements, builds monthly 24x7 agent schedules, processes agent leave, and supports safe shift swaps.

**Version:** `4.0.0`  
**Default local URL:** `http://127.0.0.1:8000`

## Main workflow

```text
Historical CDR CSV file(s)
        ↓
CDR validation and cleaning
        ↓
Continuous interval dataset
        ↓
STL decomposition forecast
        ↓
Forecasted call volume + AHT
        ↓
Erlang C staffing requirements
        ↓
Dashboard summaries/charts
        ↓
Monthly agent schedule
        ↓
Leave management
        ↓
Shift swap management
```

The old standalone AHT, traffic, Erlang-output, required-agent, single-CDR forecast, and calendar-average/multi-dataset-average API endpoints are not part of the current public API.

## Features

- Upload one or more full-year historical CDR datasets.
- Clean invalid/unwanted CDR rows before forecasting.
- Forecast future call volume using robust STL decomposition.
- Use weekly seasonality by default.
- Extrapolate recent trend using linear regression.
- Estimate AHT for future intervals from historical weekday/time-slot behaviour.
- Calculate Erlang C traffic and required staffing for every forecast interval.
- Apply shrinkage to produce scheduled-agent requirements.
- Generate monthly, daily, weekday, and time-of-day dashboard aggregates.
- Convert interval staffing into three 8-hour shifts.
- Generate a monthly roster with one shift per agent per day and a five-day weekly work limit.
- Handle leave by checking coverage and automatically attempting safe replacement.
- Support shift swaps between two working agents.
- Enforce a minimum 8-hour rest period around leave-cover, shift-transfer, and shift-swap operations.
- Serve a static browser dashboard from `static/index.html` when available.
- Provide Swagger UI, ReDoc, and OpenAPI documentation automatically through FastAPI.

## Project structure

```text
project/
├── api.py                 # FastAPI routes and request/response handling
├── calculator.py          # CDR, STL, Erlang C, scheduling, leave, and swap logic
├── requirements.txt       # Python dependencies
└── static/
    └── index.html         # Frontend dashboard
```

## Requirements

Python 3.10 or later is recommended.

Current Python dependencies:

```text
pandas>=2.0
pyworkforce>=0.5.1
fastapi>=0.110
uvicorn[standard]>=0.27
python-multipart>=0.0.9
statsmodels>=0.14
numpy>=1.24
```

## Installation

Create and activate a virtual environment, then install the dependencies.

### Linux / macOS

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

### Windows PowerShell

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

## Run the application

From the directory containing `api.py`:

```bash
uvicorn api:app --reload
```

Production-style example:

```bash
uvicorn api:app --host 0.0.0.0 --port 8000
```

Open:

- Dashboard: `http://127.0.0.1:8000/`
- Swagger UI: `http://127.0.0.1:8000/docs`
- ReDoc: `http://127.0.0.1:8000/redoc`
- OpenAPI JSON: `http://127.0.0.1:8000/openapi.json`

## Public API endpoints

| Method | Endpoint | Purpose |
|---|---|---|
| `GET` | `/` | Serve `static/index.html` when present; otherwise return service information |
| `GET` | `/health` | Health/version check |
| `POST` | `/api/v1/cdr/stl-forecast` | Build STL demand forecast and Erlang C staffing forecast |
| `POST` | `/api/v1/schedule/monthly` | Generate a monthly agent roster from forecast staffing |
| `POST` | `/api/v1/schedule/leave` | Apply leave and attempt to resolve resulting staffing shortage |
| `POST` | `/api/v1/schedule/swap` | Swap two agents' shifts subject to validation |

---

# CDR input format

The CDR reader expects seven columns in this order:

| Position | Column | Meaning |
|---:|---|---|
| 1 | `source` | Calling/source identifier |
| 2 | `destination` | Destination identifier |
| 3 | `call_datetime` | Date and time of the call |
| 4 | `duration` | Call duration in `HH:MM:SS` |
| 5 | `disposition` | Call disposition such as `Answered` |
| 6 | `unique_id` | Unique call identifier |
| 7 | `caller_id` | Caller identifier |

The file is read without a header row. The application tries these formats in order:

1. UTF-16, tab separated
2. UTF-8 with BOM, comma separated
3. Latin-1, comma separated

### Required date/time format

`call_datetime` is parsed using:

```text
YYYY-Mon-DD HH:MM:SS AM/PM
```

Example:

```text
2025-Jan-15 09:42:11 AM
```

Rows whose date cannot be parsed are removed during cleaning.

### Duration format

Duration must be:

```text
HH:MM:SS
```

Examples:

```text
00:03:12
00:17:45
01:05:10
```

Invalid durations are removed.

## CDR cleaning rules

Before forecasting, the application keeps only records that satisfy all of the following:

- `call_datetime` parses successfully.
- `duration` parses successfully.
- `source` is present and non-empty.
- `destination` is present and non-empty.
- `destination`, case-insensitively, is not `s`.
- disposition is `Answered` by default.
- duration is at least 1 second by default.
- duration is at most 4 hours by default.

The cleaned data is sorted chronologically.

Each uploaded dataset must contain at least one valid answered record after cleaning.

## Historical-year validation

Each uploaded CDR file must contain exactly one unique calendar year after cleaning.

Examples:

- A file containing only 2024 records: valid.
- A file containing both 2023 and 2024 records: rejected.
- Two uploaded files that both contain 2024: rejected as a duplicate year.

The forecast output year is:

```text
latest historical year + 1
```

For example, historical files for 2022, 2023, and 2024 produce a forecast beginning in 2025.

---

# STL forecasting

Endpoint:

```text
POST /api/v1/cdr/stl-forecast
```

Content type:

```text
multipart/form-data
```

## STL inputs

| Field | Type | Default | Validation / meaning |
|---|---|---:|---|
| `files` | repeated uploaded file | required | One or more full-year CDR files |
| `interval_minutes` | integer | `30` | Must be positive and divide 1440 exactly |
| `forecast_days` | integer | `365` | Must be greater than 0 |
| `seasonal_period` | integer or null | automatic | If supplied, must be at least 2; value is in intervals, not days |
| `trend_lookback_days` | integer | `90` | Must be at least 7 |
| `target_seconds` | number | `20` | Erlang C answer-time target; must be non-negative |
| `target_service_level` | number/percent | `80` | Must resolve to a value strictly between 0% and 100% |
| `shrinkage` | number/percent | `30` | Must be >= 0% and < 100% |
| `max_agents` | integer | `1000` | Must be > 0; forecast fails if calculated staffing exceeds it |
| `include_forecast_rows` | boolean | `true` | If false, summary/charts are returned but the interval `forecast` array is empty |

### Interval validation

`interval_minutes` must be a positive divisor of 1440. Examples of valid values include:

```text
5, 10, 15, 20, 30, 60, 120, 240, 480
```

A value such as `35` is rejected because 1440 is not evenly divisible by 35.

## How the historical interval series is built

For every uploaded year, the application:

1. reads and cleans the CDR file;
2. confirms that it contains exactly one calendar year;
3. rejects duplicate years;
4. groups calls into fixed time intervals;
5. calculates interval call volume;
6. calculates total handle time;
7. calculates interval AHT as total handle time divided by call volume;
8. rebuilds a complete continuous interval grid, including intervals with zero calls; and
9. removes February 29 before the historical years are concatenated.

For empty intervals, call volume and total handle time are zero. AHT remains missing for those intervals until later historical-slot logic supplies forecast AHT values.

## STL seasonal period

The default STL seasonal period is one week:

```text
intervals_per_day = 1440 / interval_minutes
seasonal_period = intervals_per_day × 7
```

At the default 30-minute interval:

```text
48 intervals/day × 7 days = 336 intervals
```

The application requires at least two complete seasonal cycles of historical interval data.

For a default 30-minute weekly cycle, that means at least:

```text
336 × 2 = 672 historical intervals
```

## What STL does

The historical `call_volume` series is decomposed using robust STL into:

- trend;
- seasonal component; and
- residual component.

The future forecast is created by:

1. taking the most recent trend section defined by `trend_lookback_days`;
2. fitting a straight line to that trend using linear regression;
3. extrapolating the trend for the forecast horizon;
4. taking the final STL seasonal cycle;
5. repeating that seasonal cycle through the future horizon;
6. adding projected trend and repeated seasonality;
7. rounding the result to whole calls; and
8. clipping negative values to zero.

This implementation forecasts call volume only from trend + seasonal components. The residual/noise component is not projected forward.

## Future AHT calculation

Future AHT is not produced by STL.

Instead, historical intervals are mapped into a weekly slot based on:

```text
weekday + time of day
```

The application calculates weighted historical AHT for every weekly slot using:

```text
total historical handle time / total historical calls
```

Future intervals inherit the AHT of their corresponding historical weekly slot.

If a slot has no usable historical AHT, the application falls back to the global weighted historical AHT.

## Erlang C staffing calculation

For each forecast interval, offered traffic is calculated as:

```text
traffic_erlangs = call_volume × aht_seconds / interval_seconds
```

The `pyworkforce.queuing.ErlangC` model then calculates staffing using:

- forecast call volume;
- forecast AHT;
- interval duration;
- answer-time target;
- target service level; and
- shrinkage.

The output contains both:

- `raw_agents`: positions required before shrinkage; and
- `scheduled_agents`: positions after shrinkage.

The application also returns:

- service level;
- probability of waiting;
- occupancy; and
- Average Speed of Answer (ASA).

For intervals with zero calls, staffing and traffic are returned as zero, service level as 100%, waiting probability as 0%, occupancy as 0%, and ASA as 0.

## STL forecast output columns

Each interval row contains:

| Field | Meaning |
|---|---|
| `interval_start` | Forecast interval timestamp |
| `date` | Date string |
| `day_of_year` | Sequential forecast-day number |
| `month` | Month number |
| `day` | Day of month |
| `weekday` | Python weekday number (`0=Monday`) |
| `weekday_name` | Weekday text |
| `hour` | Interval hour |
| `minute` | Interval minute |
| `call_volume` | Forecast calls |
| `aht_seconds` | Forecast AHT |
| `traffic_erlangs` | Offered load |
| `raw_agents` | Required agents before shrinkage |
| `scheduled_agents` | Required scheduled agents after shrinkage |
| `service_level_percent` | Achieved service level |
| `probability_waiting_percent` | Probability of waiting |
| `occupancy_percent` | Agent occupancy |
| `asa_seconds` | Average Speed of Answer |

## STL summary output

The response also contains summary values including:

- forecasting method;
- forecasting logic description;
- dataset count;
- historical years;
- output year;
- forecast days;
- interval length;
- seasonal period;
- trend lookback;
- number of forecast intervals;
- total predicted calls;
- weighted average AHT;
- maximum raw agents;
- maximum scheduled agents;
- average service level;
- average occupancy;
- average ASA;
- number of intervals below the target service level;
- peak interval;
- per-source-dataset cleaning summary; and
- STL decomposition summary such as final trend value, trend slope, seasonal range, and residual standard deviation.

## Dashboard aggregates

The STL endpoint also returns a `charts` object with:

### Monthly

- total call volume;
- maximum scheduled agents;
- average occupancy percentage;
- average service-level percentage.

### Daily

- total call volume;
- maximum scheduled agents;
- average service level;
- average occupancy;
- average ASA.

### Weekday

- average interval call volume by weekday;
- maximum scheduled agents by weekday.

### Time of day

- average call volume by clock time;
- average scheduled agents by clock time.

---

# Monthly scheduling

Endpoint:

```text
POST /api/v1/schedule/monthly
```

Content type:

```text
application/json
```

## Request body

```json
{
  "forecast": [
    {
      "interval_start": "2025-01-01T00:00:00",
      "scheduled_agents": 12
    }
  ],
  "year": 2025,
  "month": 1,
  "agent_count": null
}
```

## Inputs

| Field | Type | Required | Validation |
|---|---|---|---|
| `forecast` | array of objects | yes | Must not be empty and must contain required forecast columns used by scheduling |
| `year` | integer | yes | Used to filter forecast rows |
| `month` | integer | yes | Pydantic validation: 1 through 12 |
| `agent_count` | integer or null | no | If supplied, must be > 0 and not less than calculated minimum headcount |

## Shift definitions

The scheduler uses three fixed eight-hour shifts:

| Code | Shift | Time |
|---|---|---|
| `NIGHT` | Night | `00:00-08:00` |
| `MORNING` | Morning | `08:00-16:00` |
| `EVENING` | Evening | `16:00-00:00` |

## How shift requirements are calculated

For each date and shift, the scheduler looks at all forecast intervals inside that eight-hour shift.

The shift's required staffing is:

```text
maximum scheduled_agents value inside the shift
```

Using the maximum protects the shift against the busiest forecast interval within that shift.

## Minimum headcount calculation

The application estimates required employee headcount week by week.

For each Monday-Sunday week it calculates:

```text
weekly_capacity_headcount = ceil(total required shift assignments / 5)
```

It also calculates the maximum total staffing required on any single day of that week.

The minimum headcount is the largest value found across:

- weekly capacity headcount; and
- maximum daily staffing requirement.

## Monthly roster rules

The roster generator applies these rules:

- each agent works at most one shift per date;
- each agent works at most five days in a Monday-Sunday week;
- an agent not selected for a work shift on a date receives an `OFF` row;
- assignments are ranked using weekly work count, total assignment count, shift-specific assignment count, and agent ID to spread assignments.

If `agent_count` is omitted, the calculated minimum headcount is used automatically.

If the supplied `agent_count` is below the calculated minimum, the request is rejected.

## Schedule row output

Working row example:

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

Off-day example:

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

## Monthly schedule summary

The summary contains:

- year;
- month;
- minimum agents;
- requested/used agent count;
- shift length (`8` hours);
- working days per week (`5`);
- days off per week (`2`);
- total required shift assignments;
- total assigned shift assignments;
- total coverage shortage;
- overall `coverage_ok`; and
- detailed date/shift coverage rows.

---

# Leave management

Endpoint:

```text
POST /api/v1/schedule/leave
```

## Request body

```json
{
  "forecast": [...],
  "schedule": [...],
  "agent_id": "Agent 005",
  "leave_date": "2025-01-15"
}
```

## Inputs and validation

- `forecast` must not be empty.
- `schedule` must not be empty.
- the forecast must contain `interval_start`.
- the leave date must be parseable by pandas.
- the schedule must contain `agent_id`, `date`, `shift_code`, `shift`, and `status`.
- the requested agent must have exactly one schedule row for the leave date.

The API derives the leave year/month from `leave_date`, rebuilds shift requirements for that month from the supplied forecast, and then runs the leave-resolution process.

## Leave-resolution sequence

### 1. Mark leave

If the agent is working:

- the original shift code and shift label are saved;
- `shift_code` becomes `LEAVE`;
- `shift` becomes `LEAVE`;
- `status` becomes `LEAVE`.

If the agent is already `OFF`, no replacement is required.

If the agent is already on `LEAVE`, the operation reports that no new leave action is required.

### 2. Recalculate coverage

The system finds the required staffing for the original shift/date and counts the agents still working that shift.

```text
shortage = max(required_agents - assigned_agents, 0)
```

If coverage remains sufficient, leave is resolved without a replacement.

### 3. Try an OFF-agent replacement

The system searches for agents who:

- are not the leave agent;
- are `OFF` on the leave date; and
- would not exceed five working days in that Monday-Sunday week after accepting the cover shift.

Candidates are ranked by:

1. fewer current weekly working days;
2. fewer monthly working days;
3. agent ID.

The highest-ranked candidate is assigned to the leave agent's original shift.

Audit fields may be added:

- `previous_shift_code`;
- `previous_shift`;
- `assignment_type`.

A leave-cover assignment uses:

```text
assignment_type = LEAVE_COVER
```

The replacement is then checked against the minimum rest-period rule.

### 4. Try a safe shift transfer

If no OFF agent is available, the application checks agents working another shift on the same date.

A working agent is eligible to move only when removing them from their original shift does not cause that original shift to fall below its required staffing.

Candidates are ranked by:

1. more spare agents in the source shift;
2. fewer monthly working days;
3. agent ID.

The selected agent is moved to the leave shift with:

```text
assignment_type = SHIFT_TRANSFER
```

The transfer must also pass rest validation.

### 5. Unresolved leave

If no safe OFF-agent replacement and no safe shift transfer is available, leave remains marked but the operation returns:

```text
resolved = false
method = UNRESOLVED
```

Possible result methods include:

```text
NO_ACTION
NO_REPLACEMENT_REQUIRED
OFF_AGENT_COVER
SHIFT_TRANSFER
UNRESOLVED
```

---

# Shift swaps

Endpoint:

```text
POST /api/v1/schedule/swap
```

## Request body

```json
{
  "schedule": [...],
  "agent_1": "Agent 003",
  "agent_2": "Agent 010",
  "swap_date": "2025-01-20"
}
```

## Shift-swap validation

The swap is rejected when:

- the schedule is empty;
- both selected agent IDs are the same;
- either agent has no schedule row for the selected date;
- either agent has multiple rows for that date;
- either agent's status is not `WORK`;
- either agent is assigned `OFF` or `LEAVE`;
- both agents already work the same shift; or
- the resulting shift assignment violates the rest rule for either agent.

When valid, the two shift assignments are exchanged.

Audit fields store the previous assignments and both rows are marked:

```text
assignment_type = SHIFT_SWAP
```

The result includes the previous and new shift codes for both agents and rest-validation details.

---

# Rest-period validation

Leave cover, shift transfer, and shift swap use the same rest validator.

Default minimum rest:

```text
8 hours
```

For the changed assignment, the system checks:

- the end of the previous day's working shift against the new shift's start; and
- the new shift's end against the next day's working shift start.

A change is rejected if either gap is less than eight hours.

The shift clock used for validation is:

```text
NIGHT   00:00 → 08:00
MORNING 08:00 → 16:00
EVENING 16:00 → 24:00
```

---

# Error handling

Expected user/data problems are normally returned as HTTP `400` responses with a readable `detail` message.

Examples include:

- missing files;
- empty uploaded file;
- invalid CDR data;
- mixed years in one dataset;
- duplicate uploaded historical years;
- invalid interval size;
- insufficient STL history;
- invalid service-level/shrinkage settings;
- staffing above `max_agents`;
- invalid schedule month/headcount;
- missing schedule rows;
- impossible leave replacement; and
- invalid shift swap/rest period.

Unexpected forecast, schedule, leave, or swap failures are returned as HTTP `500` with an operation-specific message.

---

# Health check

```bash
curl http://127.0.0.1:8000/health
```

Expected response:

```json
{
  "status": "healthy",
  "version": "4.0.0"
}
```

# Example end-to-end use

1. Upload historical full-year CDR CSV file(s) to `/api/v1/cdr/stl-forecast`.
2. Store the returned `forecast` array.
3. Send that forecast to `/api/v1/schedule/monthly` with the forecast year and desired month.
4. Store the returned `schedule` array.
5. For leave, send the current forecast + current schedule to `/api/v1/schedule/leave`.
6. Replace your stored schedule with the updated schedule returned by the leave endpoint.
7. For a shift swap, send the latest schedule to `/api/v1/schedule/swap`.
8. Replace your stored schedule with the updated schedule returned by the swap endpoint.

The API itself does not persist forecast or roster state between requests; the client/dashboard supplies the forecast and schedule objects required by later scheduling operations.
