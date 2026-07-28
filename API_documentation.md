# Erlang C Multi-Dataset Forecast API Documentation

## 1. Overview

This FastAPI service exposes Erlang C calculations, single-file CDR forecasting, and multi-dataset CDR averaging through HTTP endpoints.

**Application metadata**

- Title: `Erlang C Multi-Dataset Forecast API`
- Version: `3.0.0`
- Local base URL: `http://127.0.0.1:8000`

Start the service from the directory containing `api.py` and `calculator.py`:

```bash
uvicorn api:app --reload
```

Interactive documentation is generated automatically:

- Swagger UI: `GET /docs`
- ReDoc: `GET /redoc`
- OpenAPI schema: `GET /openapi.json`

## 2. Endpoint summary

| Method | Path | Content type | Purpose |
|---|---|---|---|
| `GET` | `/` | — | Return the dashboard HTML when available, otherwise basic service information |
| `GET` | `/health` | — | Return service health and version |
| `POST` | `/api/v1/aht` | `application/json` | Calculate Average Handle Time |
| `POST` | `/api/v1/traffic` | `application/json` | Calculate offered traffic in Erlangs |
| `POST` | `/api/v1/erlang-outputs` | `application/json` | Calculate waiting, ASA, service level, and occupancy |
| `POST` | `/api/v1/required-agents` | `application/json` | Calculate raw and scheduled staffing |
| `POST` | `/api/v1/cdr/forecast` | `multipart/form-data` | Build an interval forecast from one CDR file |
| `POST` | `/api/v1/cdr/multi-dataset-forecast` | `multipart/form-data` | Average two or more yearly datasets into one 365-day forecast |
| `GET` | `/static/{path}` | — | Serve files from the application’s `static` directory |

## 3. General endpoints

### GET `/`

The root route first checks for `static/index.html`.

- When that file exists, it returns the HTML dashboard as a file response.
- When it does not exist, it returns JSON:

```json
{
  "message": "Erlang C Multi-Dataset Forecast API",
  "documentation": "/docs"
}
```

> The supplied dashboard file must be placed at `static/index.html` relative to `api.py` for this route to display it.

### GET `/health`

Returns a simple liveness response.

```json
{
  "status": "healthy",
  "version": "3.0.0"
}
```

## 4. Calculation endpoints

### POST `/api/v1/aht`

Calculates Average Handle Time.

```text
AHT = total_handle_time_seconds / total_answered_calls
```

#### Request body

```json
{
  "total_handle_time_seconds": 10000,
  "total_answered_calls": 50
}
```

| Field | Type | Validation |
|---|---|---|
| `total_handle_time_seconds` | number | Greater than `0` |
| `total_answered_calls` | integer | Greater than `0` |

#### Response

```json
{
  "aht_seconds": 200.0
}
```

The result is rounded to two decimal places.

#### cURL

```bash
curl -X POST "http://127.0.0.1:8000/api/v1/aht" \
  -H "Content-Type: application/json" \
  -d '{
    "total_handle_time_seconds": 10000,
    "total_answered_calls": 50
  }'
```

---

### POST `/api/v1/traffic`

Calculates offered traffic in Erlangs.

```text
traffic_erlangs = call_volume × aht_seconds / interval_seconds
```

#### Request body

```json
{
  "call_volume": 100,
  "aht_seconds": 180,
  "interval_seconds": 3600
}
```

| Field | Type | Validation |
|---|---|---|
| `call_volume` | number | Zero or greater |
| `aht_seconds` | number | Greater than `0` |
| `interval_seconds` | number | Greater than `0` |

#### Response

```json
{
  "traffic_erlangs": 5.0
}
```

The result is rounded to four decimal places.

---

### POST `/api/v1/erlang-outputs`

Calculates Erlang C performance metrics for a supplied traffic level and agent count.

#### Request body

```json
{
  "traffic_erlangs": 5,
  "agents": 8,
  "aht_seconds": 180,
  "target_seconds": 20
}
```

| Field | Type | Validation |
|---|---|---|
| `traffic_erlangs` | number | Zero or greater |
| `agents` | integer | Greater than `0` |
| `aht_seconds` | number | Greater than `0` |
| `target_seconds` | number | Zero or greater |

#### Response fields

| Field | Description |
|---|---|
| `traffic_erlangs` | Supplied offered traffic |
| `agents` | Supplied agent count |
| `probability_waiting_percent` | Estimated percentage of calls that wait |
| `asa_seconds` | Average Speed of Answer; `null` when the system is overloaded |
| `service_level_percent` | Percentage answered within the target time |
| `occupancy_percent` | Estimated agent occupancy percentage |

Example response shape:

```json
{
  "traffic_erlangs": 5,
  "agents": 8,
  "probability_waiting_percent": 16.73,
  "asa_seconds": 10.04,
  "service_level_percent": 88.01,
  "occupancy_percent": 62.5
}
```

The actual numeric values are calculated by the installed `pyworkforce` implementation.

When `agents <= traffic_erlangs`, the calculation treats the system as overloaded:

- waiting probability is `100%`;
- service level is `0%`;
- `asa_seconds` is `null`;
- occupancy may exceed `100%` because it is calculated as traffic divided by agents.

> Unlike the older documentation, this endpoint returns percentage fields only. It does not return decimal fields named `probability_waiting`, `service_level`, or `occupancy`.

---

### POST `/api/v1/required-agents`

Calculates the staffing required to meet a target service level.

- `raw_agents`: agents required actively available to handle calls.
- `scheduled_agents`: rostered agents after shrinkage is applied.

#### Request body

```json
{
  "call_volume": 100,
  "aht_seconds": 180,
  "interval_seconds": 3600,
  "target_seconds": 20,
  "target_service_level": 80,
  "shrinkage": 30,
  "max_agents": 1000
}
```

| Field | Type | Default | Validation |
|---|---|---:|---|
| `call_volume` | number | required | Zero or greater |
| `aht_seconds` | number | required | Greater than `0` |
| `interval_seconds` | number | required | Greater than `0` |
| `target_seconds` | number | required | Zero or greater |
| `target_service_level` | number | required | Greater than `0`; normalized later to a fraction between 0 and 1 |
| `shrinkage` | number | `0` | Zero or greater; normalized later to a fraction below 1 |
| `max_agents` | integer | `1000` | Greater than `0` |

Percentage normalization performed by the calculator:

- `80` and `0.80` both represent 80%.
- `30` and `0.30` both represent 30%.
- JSON strings such as `"80%"` are not accepted by these numeric Pydantic request fields.
- A target service level of exactly `100` or `1.0` is rejected because the implementation requires a value strictly below 100%.
- Shrinkage must be below 100%.

#### Response

```json
{
  "traffic_erlangs": 5.0,
  "raw_agents": 8,
  "scheduled_agents": 12,
  "service_level_percent": 85.0,
  "probability_waiting_percent": 17.0,
  "occupancy_percent": 62.5,
  "asa_seconds": 10.2
}
```

> The endpoint does not return decimal versions of service level, waiting probability, or occupancy.

When `call_volume` is `0`, the result is zero agents, zero traffic, zero ASA, zero occupancy, and a 100% service level.

## 5. Single-file CDR forecast

### POST `/api/v1/cdr/forecast`

Uploads one CDR file, cleans its records, groups valid answered calls into intervals, and calculates staffing for each non-empty interval.

#### Form fields

| Field | Type | Default | Description |
|---|---|---:|---|
| `file` | file | required | CDR CSV or tab-separated file |
| `interval_minutes` | integer | `60` | Interval size; must be a positive divisor of 1440 |
| `target_seconds` | number | `20` | Target answer time in seconds |
| `target_service_level` | number | `80` | Target service level, as `80` or `0.80` |
| `shrinkage` | number | `30` | Shrinkage, as `30` or `0.30` |

Although the API model itself does not constrain all form fields, the calculator validates them during processing.

Valid examples for `interval_minutes` include `15`, `30`, `60`, `120`, and `1440`. A value such as `50` is rejected because it does not divide evenly into one day.

#### cURL

```bash
curl -X POST "http://127.0.0.1:8000/api/v1/cdr/forecast" \
  -F "file=@calls.csv" \
  -F "interval_minutes=30" \
  -F "target_seconds=20" \
  -F "target_service_level=80" \
  -F "shrinkage=30"
```

#### Response

```json
{
  "filename": "calls.csv",
  "interval_minutes": 30,
  "valid_call_count": 12500,
  "interval_count": 7340,
  "forecast": [
    {
      "interval_start": "2025-01-15T09:00:00",
      "call_volume": 50,
      "aht_seconds": 180.0,
      "traffic_erlangs": 2.5,
      "raw_agents": 5,
      "scheduled_agents": 8,
      "service_level_percent": 85.2,
      "probability_waiting_percent": 14.8,
      "occupancy_percent": 50.0,
      "asa_seconds": 12.45
    }
  ]
}
```

Only intervals containing at least one valid call are returned.

## 6. Multi-dataset 365-day forecast

### POST `/api/v1/cdr/multi-dataset-forecast`

Uploads at least two full-year CDR datasets and creates one 365-day average forecast.

For every matching month, day, hour, and minute interval, the service:

1. builds a complete interval series for each source year;
2. removes February 29;
3. averages call volume across years;
4. calculates a weighted AHT using all calls in the matching interval;
5. generates an output year equal to the latest historical year plus one;
6. runs Erlang C staffing calculations for every interval; and
7. returns summary metrics, chart aggregates, and optionally all interval rows.

### Hidden alias


#### Form fields

| Field | Type | Default | Description |
|---|---|---:|---|
| `files` | repeated file field | required | At least two CDR files; send each under the same `files` field name |
| `interval_minutes` | integer | `30` | Positive divisor of 1440 |
| `target_seconds` | number | `20` | Target answer time in seconds |
| `target_service_level` | number | `80` | Target service level |
| `shrinkage` | number | `30` | Shrinkage |
| `max_agents` | integer | `1000` | Maximum permitted raw or scheduled agent count |
| `include_forecast_rows` | boolean | `true` | Return all interval rows when true; return an empty `forecast` array when false |

#### Dataset rules

Each uploaded dataset must:

- contain at least one valid answered record;
- contain exactly one calendar year after cleaning;
- use a year not already present in another uploaded file;
- provide calendar coverage sufficient to build all 365 days of the output pattern.

Duplicate source years are rejected. Leap-day intervals are deliberately excluded, so the generated result always contains 365 days.

#### cURL

```bash
curl -X POST "http://127.0.0.1:8000/api/v1/cdr/multi-dataset-forecast" \
  -F "files=@calls_2023.csv" \
  -F "files=@calls_2024.csv" \
  -F "interval_minutes=30" \
  -F "target_seconds=20" \
  -F "target_service_level=80" \
  -F "shrinkage=30" \
  -F "max_agents=1000" \
  -F "include_forecast_rows=true"
```

#### Top-level response structure

```json
{
  "logic": "Average matching month/day/time intervals across all uploaded yearly datasets.",
  "dataset_count": 2,
  "historical_years": [2023, 2024],
  "output_year": 2025,
  "days": 365,
  "interval_minutes": 30,
  "forecast_interval_count": 17520,
  "total_predicted_calls": 250000,
  "average_aht_seconds": 182.4,
  "maximum_raw_agents": 42,
  "maximum_scheduled_agents": 60,
  "average_service_level_percent": 83.1,
  "average_occupancy_percent": 71.5,
  "average_asa_seconds": 12.7,
  "intervals_below_service_target": 14,
  "peak_interval": {
    "interval_start": "2025-12-20T10:30:00",
    "call_volume": 190,
    "scheduled_agents": 60
  },
  "source_data": {
    "dataset_count": 2,
    "historical_years": [2023, 2024],
    "datasets": [],
    "total_valid_answered_calls": 500000,
    "global_weighted_aht_seconds": 181.9
  },
  "parameters": {
    "interval_minutes": 30,
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

The values above illustrate the shape only.

#### `source_data.datasets` record

Each source dataset summary contains:

| Field | Description |
|---|---|
| `filename` | Original uploaded filename |
| `year` | Detected calendar year |
| `raw_rows` | Number of rows read before cleaning |
| `valid_answered_calls` | Number of retained records |
| `removed_rows` | Rows removed by cleaning |
| `average_aht_seconds` | Average duration of valid answered calls |
| `first_call` | Earliest retained call timestamp |
| `last_call` | Latest retained call timestamp |

#### Forecast row fields

When `include_forecast_rows=true`, each row includes:

| Field | Description |
|---|---|
| `interval_start` | Output interval timestamp |
| `date` | Date as `YYYY-MM-DD` |
| `day_of_year` | 1-based output day number |
| `month`, `day` | Calendar month and day |
| `weekday` | Monday=`0` through Sunday=`6` |
| `weekday_name` | English weekday name |
| `hour`, `minute` | Interval time components |
| `call_volume` | Rounded average call count |
| `aht_seconds` | Weighted predicted AHT |
| `traffic_erlangs` | Offered traffic |
| `raw_agents` | Required active agents |
| `scheduled_agents` | Staffing after shrinkage |
| `service_level_percent` | Achieved service level |
| `probability_waiting_percent` | Probability of waiting |
| `occupancy_percent` | Agent occupancy |
| `asa_seconds` | Average Speed of Answer |

#### Chart aggregate fields

`charts` contains four arrays:

- `monthly`: monthly calls, maximum scheduled agents, average occupancy, and average service level;
- `daily`: daily calls, maximum scheduled agents, average service level, average occupancy, and average ASA;
- `weekday`: average calls and maximum scheduled agents by weekday;
- `time_of_day`: average calls and average scheduled agents by time interval.

Setting `include_forecast_rows=false` is useful when a client needs only the summaries and chart data, because the full 365-day interval array can be large.

## 7. CDR input contract

The reader expects exactly seven columns with no header row, in this order:

```text
source,destination,call_datetime,duration,disposition,unique_id,caller_id
```

Example:

```text
1001,2001,2025-Jan-15 03:25:10 PM,00:03:45,Answered,abc123,0771234567
```

### Date format

```text
YYYY-Mon-DD HH:MM:SS AM/PM
```

Example: `2025-Jan-15 03:25:10 PM`

### Duration format

```text
HH:MM:SS
```

### File-reading attempts

The service tries these formats in order:

1. UTF-16, tab-separated;
2. UTF-8 with BOM, comma-separated;
3. Latin-1, comma-separated.

## 8. CDR cleaning rules

By default, a row is retained only when:

- `call_datetime` matches the expected format;
- `duration` can be converted from `HH:MM:SS`;
- source is present and non-empty;
- destination is present and is not `s`, case-insensitively;
- disposition is `Answered`, case-insensitively after trimming; and
- duration is between 1 second and 14,400 seconds, inclusive.

Cleaned rows are sorted by call time.

CDR duration is treated as handle time. If after-call work is absent from the source data, AHT and staffing may be understated.

## 9. Status codes and errors

### HTTP 200

The request completed successfully.

### HTTP 400 — application validation or calculation error

Typical cases include:

- an empty uploaded file;
- fewer than two files for the multi-dataset endpoint;
- no valid answered records;
- invalid interval size;
- multiple years in one file;
- duplicate source years;
- incomplete 365-day calendar coverage;
- invalid percentage normalization;
- shrinkage of 100% or more;
- target service level of 100% or more;
- required staffing exceeding `max_agents`.

Typical response:

```json
{
  "detail": "Upload at least two yearly CDR datasets."
}
```

### HTTP 422 — FastAPI request validation error

Returned when required body/form fields are missing, values cannot be parsed into the declared types, or Pydantic constraints fail.

```json
{
  "detail": [
    {
      "type": "greater_than",
      "loc": ["body", "agents"],
      "msg": "Input should be greater than 0",
      "input": 0
    }
  ]
}
```

The exact error format may vary with the installed FastAPI and Pydantic versions.

### HTTP 500 — unexpected processing error

Single-file failures use a detail beginning with:

```text
Forecast failed: ...
```

Multi-dataset failures use:

```text
Multi-dataset forecast failed: ...
```

For production, log the underlying exception server-side and return a less detailed public error message.

## 10. Static dashboard

The application mounts the local `static` directory at `/static` and expects the main dashboard at:

```text
static/index.html
```

The supplied dashboard submits data to:

```text
POST /api/v1/cdr/multi-dataset-forecast
```

It displays summary cards, Chart.js charts, the first 500 forecast rows, and a browser-generated CSV containing all returned forecast rows.

## 11. Python example

```python
import requests

payload = {
    "call_volume": 100,
    "aht_seconds": 180,
    "interval_seconds": 3600,
    "target_seconds": 20,
    "target_service_level": 80,
    "shrinkage": 30,
    "max_agents": 1000,
}

response = requests.post(
    "http://127.0.0.1:8000/api/v1/required-agents",
    json=payload,
    timeout=30,
)
response.raise_for_status()
print(response.json())
```

## 12. Deployment and security recommendations

The current code has no built-in authentication or authorization. Before public deployment, consider adding:

- authentication and endpoint authorization;
- CORS restrictions;
- request and upload size limits;
- stricter file-type and content checks;
- rate limiting;
- structured logs and request tracing;
- metrics, health monitoring, and alerting;
- environment-based settings;
- automated API and calculation tests;
- a production ASGI process configuration; and
- a reverse proxy with TLS.

## 13. Dependency baseline

The supplied requirements specify:

```text
pandas>=2.0
pyworkforce>=0.5.1
fastapi>=0.110
uvicorn[standard]>=0.27
python-multipart>=0.0.9
```

Because these are minimum versions rather than exact pins, generated validation messages and calculation behavior may differ slightly across environments.
