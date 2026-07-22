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
- Upload a CDR file and receive a JSON forecast through the API.
- Use automatically generated Swagger and ReDoc API documentation.

## Project structure

Use these conventional filenames in the project directory:

```text
.
├── api.py                 # FastAPI application
├── calculator.py          # Erlang C calculations, CDR processing, and CLI
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
| `POST` | `/api/v1/cdr/forecast` | Upload a CDR file and build an interval forecast |

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

Upload a CDR file:

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
- Only intervals containing valid answered calls are returned.
- The API returns `null` for ASA when the supplied agent count does not exceed traffic in the `/api/v1/erlang-outputs` calculation.
- The current implementation has no authentication, authorization, rate limiting, persistent storage, or background job processing.

## Further documentation

See [API_DOCUMENTATION.md](API_DOCUMENTATION.md) for endpoint-level documentation, validation rules, status codes, examples, and processing details.
