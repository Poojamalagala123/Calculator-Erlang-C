# Erlang C Calculator and API

A Python toolkit for call-centre workload analysis and staffing forecasts using Erlang C. The project supports:

- an interactive command-line calculator;
- batch processing of Call Detail Record (CDR) CSV files; and
- a FastAPI REST service for calculator and CDR forecasting operations.

## Main capabilities

- Calculate Average Handle Time (AHT).
- Calculate offered traffic in Erlangs.
- Calculate probability of waiting, Average Speed of Answer (ASA), service level, and occupancy.
- Calculate raw and scheduled agent requirements, including shrinkage.
- Read and clean CDR files in several common encodings.
- Aggregate answered calls into 15-, 30-, 60-minute, or custom intervals.
- Export interval staffing forecasts to CSV through the CLI.
- Upload a single CDR file and receive an interval forecast through the API.
- Upload two or more full-year CDR datasets and generate one averaged 365-day Erlang C forecast.
- View multi-dataset results in the included browser dashboard and export forecast rows to CSV.
- Use automatically generated Swagger and ReDoc API documentation.

## Project structure

Use these conventional filenames in the project directory:

```text
.
├── api.py                 # FastAPI application
├── calculator.py          # Erlang C calculations, CDR processing, and CLI
├── static/
│   └── index.html          # Browser dashboard
├── README.md
└── API_DOCUMENTATION.md
```

The API imports functions from `calculator`, so the calculator module must be named `calculator.py` and be in the same directory as `api.py`.

## Requirements

- Python 3.9 or newer
- `pandas`
- `pyworkforce`
- `fastapi`
- `uvicorn`
- `python-multipart`

Install the dependencies:

```bash
python -m pip install pandas pyworkforce fastapi uvicorn python-multipart
```

A virtual environment is recommended:

```bash
python -m venv .venv
```

Activate it on Windows:

```powershell
.venv\Scripts\activate
```

Activate it on macOS or Linux:

```bash
source .venv/bin/activate
```

## Run the command-line calculator

```bash
python calculator.py
```

The menu provides the following operations:

```text
A - Calculate AHT
T - Calculate Traffic / Offered Load
E - Calculate Erlang C outputs
R - Calculate Required Agents
S - Show CDR summary
C - Process CDR CSV file
Q - Quit
```

### CLI calculations

#### AHT

```text
AHT = total handle time in seconds / answered calls
```

#### Traffic

```text
Traffic (Erlangs) = call volume × AHT / interval seconds
```

#### Required agents

The required-agent calculation uses:

- forecast call volume;
- AHT in seconds;
- interval length;
- target answer time;
- target service level; and
- shrinkage.

`raw_agents` is the number of agents required to be actively available. `scheduled_agents` includes the additional staffing required for shrinkage.

Service level and shrinkage can be supplied as decimal or whole-number percentages. For example, `0.80`, `80`, and `80%` all represent 80% in the CLI calculation functions.

## Run the API

From the project directory:

```bash
uvicorn api:app --reload
```

The default local URLs are:

- API root: `http://127.0.0.1:8000/`
- Health check: `http://127.0.0.1:8000/health`
- Swagger UI: `http://127.0.0.1:8000/docs`
- ReDoc: `http://127.0.0.1:8000/redoc`

For a production-style launch, omit `--reload` and configure an appropriate host, port, process manager, and reverse proxy.

## API overview

| Method | Endpoint | Purpose |
|---|---|---|
| `GET` | `/` | Basic service information |
| `GET` | `/health` | Health check |
| `POST` | `/api/v1/aht` | Calculate Average Handle Time |
| `POST` | `/api/v1/traffic` | Calculate traffic in Erlangs |
| `POST` | `/api/v1/erlang-outputs` | Calculate Erlang C performance outputs |
| `POST` | `/api/v1/required-agents` | Calculate raw and scheduled agents |
| `POST` | `/api/v1/cdr/forecast` | Upload one CDR file and build a non-empty interval forecast |
| `POST` | `/api/v1/cdr/multi-dataset-forecast` | Average two or more full-year datasets into one 365-day forecast |

Detailed request and response examples are available in [API_DOCUMENTATION.md](API_DOCUMENTATION.md).

## CDR input format

The CDR reader expects seven columns without a header row, in this order:

```text
source,destination,call_datetime,duration,disposition,unique_id,caller_id
```

Example:

```text
1001,2001,2025-Jan-15 03:25:10 PM,00:03:45,Answered,abc123,0771234567
```

Supported read attempts are:

1. UTF-16 tab-separated;
2. UTF-8 with BOM, comma-separated; and
3. Latin-1, comma-separated.

The expected date format is:

```text
YYYY-Mon-DD HH:MM:SS AM/PM
```

The duration format is:

```text
HH:MM:SS
```

## CDR cleaning rules

By default, preprocessing keeps records only when:

- the date and duration can be parsed;
- source and destination are present;
- disposition is exactly `Answered`;
- duration is between 1 second and 4 hours; and
- destination is not `s`, ignoring letter case.

The cleaned calls are sorted by call time before interval aggregation.


## Multi-dataset 365-day forecast

`POST /api/v1/cdr/multi-dataset-forecast` accepts at least two yearly CDR files and produces one averaged 365-day Erlang C forecast.

The multi-dataset process:

1. cleans each uploaded CDR file;
2. verifies that each file contains exactly one calendar year;
3. rejects duplicate years;
4. creates a complete interval grid for every source year;
5. excludes February 29 so the output always contains 365 days;
6. averages matching month, day, hour, and minute intervals across all datasets;
7. calculates a weighted AHT for every interval;
8. runs Erlang C staffing calculations; and
9. returns summary, chart, source-data, and optional detailed forecast records.

The output year is selected automatically as the year after the latest uploaded historical year. For example, datasets for 2023, 2024, and 2025 produce a forecast labeled as 2026.

### Multi-dataset request fields

The endpoint uses `multipart/form-data`.

| Field | Type | Required | Default | Description |
|---|---|---:|---:|---|
| `files` | file array | yes | — | Two or more full-year CDR files; repeat this form key once per file |
| `interval_minutes` | integer | no | `30` | Positive divisor of 1440, such as 15, 30, 60, 120, or 1440 |
| `target_seconds` | number | no | `20` | Target answer time in seconds |
| `target_service_level` | number | no | `80` | Target service level; accepts `80` or `0.80` |
| `shrinkage` | number | no | `30` | Shrinkage; accepts `30` or `0.30` |
| `max_agents` | integer | no | `1000` | Maximum allowed raw or scheduled agent count |
| `include_forecast_rows` | boolean | no | `true` | Include every forecast interval when true; return an empty `forecast` array when false |

### Test the multi-dataset endpoint in Postman

1. Start the API with `uvicorn api:app --reload`.
2. Create a `POST` request to `http://127.0.0.1:8000/api/v1/cdr/multi-dataset-forecast`.
3. Select **Body → form-data**.
4. Add the following rows:

| Key | Postman type | Example value |
|---|---|---|
| `files` | File | `cdr_2023.csv` |
| `files` | File | `cdr_2024.csv` |
| `interval_minutes` | Text | `30` |
| `target_seconds` | Text | `20` |
| `target_service_level` | Text | `80` |
| `shrinkage` | Text | `30` |
| `max_agents` | Text | `1000` |
| `include_forecast_rows` | Text | `false` |

Use the exact key name `files` for every uploaded file. Do not use `file1`, `file2`, or a raw JSON body. Do not manually set the `Content-Type` header; Postman supplies the multipart boundary automatically.

Using `include_forecast_rows=false` is recommended for an initial test because a 30-minute forecast contains 17,520 detailed interval records. Summary and chart data are still returned.

### Multi-dataset curl example

```bash
curl -X POST "http://127.0.0.1:8000/api/v1/cdr/multi-dataset-forecast" \
  -F "files=@cdr_2023.csv" \
  -F "files=@cdr_2024.csv" \
  -F "interval_minutes=30" \
  -F "target_seconds=20" \
  -F "target_service_level=80" \
  -F "shrinkage=30" \
  -F "max_agents=1000" \
  -F "include_forecast_rows=false"
```

### Multi-dataset response structure

A successful response includes:

| Field | Description |
|---|---|
| `logic` | Description of the averaging method |
| `dataset_count` | Number of uploaded datasets |
| `historical_years` | Years detected in the source files |
| `output_year` | Automatically selected forecast year |
| `days` | Always `365` |
| `forecast_interval_count` | Number of generated forecast intervals |
| `total_predicted_calls` | Total forecast call volume |
| `average_aht_seconds` | Call-volume-weighted forecast AHT |
| `maximum_raw_agents` | Highest active-agent requirement |
| `maximum_scheduled_agents` | Highest rostered-agent requirement after shrinkage |
| `average_service_level_percent` | Mean achieved service level |
| `average_occupancy_percent` | Mean occupancy |
| `average_asa_seconds` | Mean Average Speed of Answer |
| `intervals_below_service_target` | Number of intervals below the requested service target |
| `peak_interval` | Highest-volume forecast interval |
| `source_data` | Per-file cleaning and source-year statistics |
| `parameters` | Request parameters used for the calculation |
| `charts` | Monthly, daily, weekday, and time-of-day aggregates |
| `forecast` | Detailed interval records, or an empty array when disabled |

Example abbreviated response:

```json
{
  "logic": "Average matching month/day/time intervals across all uploaded yearly datasets.",
  "dataset_count": 2,
  "historical_years": [2023, 2024],
  "output_year": 2025,
  "days": 365,
  "interval_minutes": 30,
  "forecast_interval_count": 17520,
  "total_predicted_calls": 125000,
  "average_aht_seconds": 184.52,
  "maximum_raw_agents": 42,
  "maximum_scheduled_agents": 60,
  "average_service_level_percent": 82.13,
  "average_occupancy_percent": 74.68,
  "average_asa_seconds": 11.42,
  "intervals_below_service_target": 15,
  "peak_interval": {
    "interval_start": "2025-12-20T10:30:00",
    "call_volume": 180,
    "scheduled_agents": 60
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

The values above are illustrative. Actual results depend on the uploaded files and Erlang C parameters.

### Common multi-dataset errors

- Fewer than two files: `Upload at least two yearly CDR datasets.`
- More than one year in a file: `<filename> must contain exactly one calendar year`.
- Duplicate source year: `Duplicate year <year>. Upload only one dataset for each year.`
- No valid answered records: `No valid Answered records found in <filename>.`
- Incomplete calendar coverage: `The uploaded datasets did not cover all 365 calendar days.`
- Invalid interval: `interval_minutes must be a positive divisor of 1440.`
- Agent requirement above the configured limit: `Required agents exceed max_agents limit.`

## Interval forecast output

For each non-empty interval, the project calculates:

| Field | Description |
|---|---|
| `interval_start` | Beginning of the interval |
| `call_volume` | Number of valid answered calls |
| `aht_seconds` | Mean call duration in seconds |
| `traffic_erlangs` | Offered workload |
| `raw_agents` | Agents required online and available |
| `scheduled_agents` | Agents to roster after shrinkage |
| `service_level_percent` | Achieved service level |
| `probability_waiting_percent` | Estimated chance that a caller waits |
| `occupancy_percent` | Estimated agent occupancy |
| `asa_seconds` | Average Speed of Answer |

## Example API request

Calculate required agents:

```bash
curl -X POST "http://127.0.0.1:8000/api/v1/required-agents" \
  -H "Content-Type: application/json" \
  -d '{
    "call_volume": 100,
    "aht_seconds": 180,
    "interval_seconds": 3600,
    "target_seconds": 20,
    "target_service_level": 80,
    "shrinkage": 30,
    "max_agents": 1000
  }'
```

Upload one CDR file:

```bash
curl -X POST "http://127.0.0.1:8000/api/v1/cdr/forecast" \
  -F "file=@calls.csv" \
  -F "interval_minutes=60" \
  -F "target_seconds=20" \
  -F "target_service_level=80" \
  -F "shrinkage=30"
```

## Important limitations

- The Erlang C model assumes a queue without abandonment and is most suitable when calls wait until answered.
- CDR duration is treated as handle time. If after-call work is not included in the source data, calculated AHT and staffing may be understated.
- The single-file endpoint returns only intervals containing valid answered calls.
- The multi-dataset endpoint requires each source file to cover one complete calendar year and always produces a non-leap 365-day output.
- The API returns `null` for ASA when the supplied agent count does not exceed traffic in the `/api/v1/erlang-outputs` calculation.
- The current implementation has no authentication, authorization, rate limiting, persistent storage, or background job processing.

## Further documentation

See [API_DOCUMENTATION.md](API_DOCUMENTATION.md) for endpoint-level documentation, validation rules, status codes, examples, and processing details.
