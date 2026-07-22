# Erlang C Calculator API Documentation

## 1. Overview

The Erlang C Calculator API exposes the calculation and CDR-processing functions in `calculator.py` through FastAPI. The application identifies itself as version `1.0.0` and provides interactive OpenAPI documentation automatically.

Base URL for local development:

```text
http://127.0.0.1:8000
```

Start the service with:

```bash
uvicorn api:app --reload
```

## 2. Interactive documentation

After starting the service:

- Swagger UI: `/docs`
- ReDoc: `/redoc`
- OpenAPI schema: `/openapi.json`

## 3. Content types

Calculator endpoints use:

```text
Content-Type: application/json
```

The CDR forecast endpoint uses multipart form data because it accepts a file:

```text
Content-Type: multipart/form-data
```

## 4. General endpoints

### GET `/`

Returns basic service links.

Example response:

```json
{
  "message": "Erlang C Calculator API",
  "documentation": "/docs",
  "health": "/health"
}
```

### GET `/health`

Returns a simple health indicator.

Example response:

```json
{
  "status": "healthy"
}
```

## 5. Calculation endpoints

### POST `/api/v1/aht`

Calculates Average Handle Time.

Formula:

```text
AHT = total_handle_time_seconds / total_answered_calls
```

Request body:

```json
{
  "total_handle_time_seconds": 10000,
  "total_answered_calls": 50
}
```

Validation:

- `total_handle_time_seconds` must be greater than `0`.
- `total_answered_calls` must be greater than `0`.

Response:

```json
{
  "aht_seconds": 200.0
}
```

### POST `/api/v1/traffic`

Calculates offered traffic in Erlangs.

Formula:

```text
traffic = call_volume × aht_seconds / interval_seconds
```

Request body:

```json
{
  "call_volume": 100,
  "aht_seconds": 180,
  "interval_seconds": 3600
}
```

Validation:

- `call_volume` must be zero or greater.
- `aht_seconds` must be greater than `0`.
- `interval_seconds` must be greater than `0`.

Response:

```json
{
  "traffic_erlangs": 5.0
}
```

### POST `/api/v1/erlang-outputs`

Calculates Erlang C performance values for a supplied traffic level and number of agents.

Request body:

```json
{
  "traffic_erlangs": 5,
  "agents": 8,
  "aht_seconds": 180,
  "target_seconds": 20
}
```

Validation:

- `traffic_erlangs` must be zero or greater.
- `agents` must be greater than `0`.
- `aht_seconds` must be greater than `0`.
- `target_seconds` must be zero or greater.

Response fields:

| Field | Description |
|---|---|
| `traffic_erlangs` | Supplied traffic |
| `agents` | Supplied agent count |
| `probability_waiting` | Waiting probability as a decimal |
| `probability_waiting_percent` | Waiting probability as a percentage |
| `asa_seconds` | Average Speed of Answer, or `null` for an unstable load |
| `service_level` | Service level as a decimal |
| `service_level_percent` | Service level as a percentage |
| `occupancy` | Occupancy as a decimal |
| `occupancy_percent` | Occupancy as a percentage |

When `agents <= traffic_erlangs`, the implementation treats the system as overloaded: waiting probability becomes `1`, service level becomes `0`, and ASA is returned as `null` by the API.

### POST `/api/v1/required-agents`

Calculates the number of active and scheduled agents required to meet a service-level target.

Request body:

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

Validation:

- `call_volume` must be zero or greater.
- `aht_seconds` must be greater than `0`.
- `interval_seconds` must be greater than `0`.
- `target_seconds` must be zero or greater.
- `target_service_level` must be greater than `0`.
- `shrinkage` must be zero or greater and must resolve to less than `100%`.
- `max_agents` must be greater than `0`.

Percentage normalization:

- `80` and `0.80` both become `0.80`.
- `30` and `0.30` both become `0.30`.
- JSON numeric fields do not accept strings such as `"80%"` through these Pydantic models.

Example response shape:

```json
{
  "traffic_erlangs": 5.0,
  "raw_agents": 8,
  "scheduled_agents": 12,
  "service_level": 0.85,
  "service_level_percent": 85.0,
  "probability_waiting": 0.17,
  "probability_waiting_percent": 17.0,
  "occupancy": 0.625,
  "occupancy_percent": 62.5,
  "asa_seconds": 10.2
}
```

The exact numerical values depend on the `pyworkforce` Erlang C implementation and request parameters.

## 6. CDR forecast endpoint

### POST `/api/v1/cdr/forecast`

Uploads a CDR file, filters invalid records, groups calls into intervals, and returns staffing calculations for each non-empty interval.

Form fields:

| Field | Type | Default | Description |
|---|---:|---:|---|
| `file` | file | required | CDR CSV or tab-separated file |
| `interval_minutes` | integer | `60` | Aggregation interval; must be greater than `0` |
| `target_seconds` | number | `20` | Target answer time |
| `target_service_level` | number | `80` | Target service level; accepts `80` or `0.80` |
| `shrinkage` | number | `30` | Shrinkage; accepts `30` or `0.30` |

Example request:

```bash
curl -X POST "http://127.0.0.1:8000/api/v1/cdr/forecast" \
  -F "file=@calls.csv" \
  -F "interval_minutes=30" \
  -F "target_seconds=20" \
  -F "target_service_level=80" \
  -F "shrinkage=30"
```

Top-level response fields:

| Field | Description |
|---|---|
| `filename` | Original uploaded filename |
| `interval_minutes` | Selected aggregation interval |
| `valid_call_count` | Number of cleaned calls used |
| `interval_count` | Number of non-empty forecast intervals |
| `forecast` | Array of interval forecast records |

Each forecast record contains:

```json
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
```

## 7. CDR data contract

The reader assigns these column names in order and assumes there is no header row:

```text
source
 destination
 call_datetime
 duration
 disposition
 unique_id
 caller_id
```

Compact CSV order:

```text
source,destination,call_datetime,duration,disposition,unique_id,caller_id
```

Expected date format:

```text
YYYY-Mon-DD HH:MM:SS AM/PM
```

Expected duration format:

```text
HH:MM:SS
```

File-reading attempts occur in this order:

1. UTF-16 with tab delimiter;
2. UTF-8 with BOM and comma delimiter;
3. Latin-1 with comma delimiter.

## 8. CDR processing flow

1. The uploaded file is read into memory.
2. An empty upload is rejected.
3. The file is written to a temporary path.
4. `preprocess_cdr()` parses and filters records.
5. The request is rejected when no valid answered calls remain.
6. `build_interval_forecast()` resamples calls into the selected interval.
7. `required_agents()` runs for each interval.
8. Results are returned as JSON.
9. The temporary file is removed in a `finally` block.

Default cleaning rules retain records where:

- `call_datetime` is parseable;
- `duration` is parseable;
- source and destination are present;
- disposition is `Answered`;
- duration is between 1 second and 14,400 seconds; and
- destination is not `s`, case-insensitively.

## 9. Error responses

### FastAPI validation error: HTTP 422

FastAPI returns `422 Unprocessable Entity` when request fields are missing, have incorrect types, or violate Pydantic field constraints.

Typical shape:

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

### Application input error: HTTP 400

The API returns `400 Bad Request` for supported runtime validation failures, including:

- invalid interval length;
- empty uploaded file;
- no valid answered calls;
- shrinkage of 100% or more;
- calculation constraints that cannot be satisfied within the agent limit; or
- unreadable CDR content.

Typical shape:

```json
{
  "detail": "interval_minutes must be greater than 0."
}
```

### Internal error: HTTP 500

Unexpected calculation or CDR-processing errors return a `500` response with a `detail` message. In a public production deployment, consider logging the internal exception while returning a less detailed client-facing error.

## 10. Python module reference

Important functions in `calculator.py`:

| Function | Purpose |
|---|---|
| `hms_to_seconds()` | Convert `HH:MM:SS` to seconds |
| `clean_percent()` | Normalize decimal or whole-number percentages |
| `calculate_aht()` | Calculate Average Handle Time |
| `calculate_traffic()` | Calculate offered load |
| `erlang_c_probability()` | Calculate waiting probability |
| `average_speed_of_answer()` | Calculate ASA |
| `service_level()` | Calculate service level |
| `occupancy()` | Calculate occupancy |
| `required_agents()` | Calculate raw and scheduled staffing |
| `read_cdr_csv()` | Try supported file encodings and delimiters |
| `preprocess_cdr()` | Parse, filter, and sort CDR records |
| `build_interval_forecast()` | Aggregate calls into intervals |
| `process_cdr_for_erlang()` | Produce and save a CLI CSV forecast |
| `show_cdr_summary()` | Print raw and cleaned CDR summary statistics |
| `menu()` | Run the interactive CLI |

## 11. Deployment and security notes

Before exposing the API publicly, consider adding:

- authentication and authorization;
- upload size limits;
- strict file-type and content validation;
- CORS configuration;
- rate limiting;
- structured application logging;
- monitoring and request tracing;
- environment-based configuration;
- automated tests; and
- a production ASGI deployment configuration.
