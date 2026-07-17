# Erlang C Calculator and CDR Staffing Tool

A command-line Python application for call-center workforce calculations using the Erlang C model. It can calculate Average Handle Time (AHT), offered traffic, service-level metrics, required staffing, and interval-level staffing forecasts from Call Detail Record (CDR) CSV files.

The application uses `pandas` for data processing and `pyworkforce` for Erlang C calculations.

## Features

- Calculate Average Handle Time (AHT)
- Calculate offered traffic in Erlangs
- Calculate:
  - Probability of waiting
  - Average Speed of Answer (ASA)
  - Service level
  - Agent occupancy
- Estimate raw and scheduled agent requirements
- Apply shrinkage to staffing calculations
- Read and clean CDR CSV files
- Aggregate calls into 15-, 30-, 60-minute, or custom intervals
- Export interval staffing forecasts to CSV
- Display a summary of raw and cleaned CDR data

## Requirements

- Python 3.9 or later
- pandas
- pyworkforce

Install the dependencies with:

```bash
pip install pandas pyworkforce
```

## Running the Application

Run the script from a terminal:

```bash
python calculator.py
```

The interactive menu will appear:

```text
===== Erlang C Calculator =====
A - Calculate AHT
T - Calculate Traffic / Offered Load
E - Calculate Erlang C outputs
R - Calculate Required Agents
S - Show CDR summary
C - Process CDR CSV file
Q - Quit
```

Enter the letter for the operation you want to perform.

## Menu Options

### A — Calculate AHT

Calculates Average Handle Time:

```text
AHT = total handle time / total answered calls
```

Example inputs:

```text
Total handle time in seconds: 18000
Total answered calls: 100
```

Result:

```text
AHT = 180.00 seconds
```

### T — Calculate Traffic

Calculates offered traffic in Erlangs:

```text
Traffic = call volume × AHT / interval length
```

Example inputs:

```text
Call volume: 120
AHT in seconds: 240
Interval in seconds: 3600
```

### E — Calculate Erlang C Outputs

Calculates Erlang C performance metrics for a specified traffic load and number of agents.

Required inputs:

- Traffic in Erlangs
- Number of agents
- AHT in seconds
- Target answer time in seconds

Outputs:

- Probability of waiting
- Average Speed of Answer (ASA)
- Service level
- Occupancy

### R — Calculate Required Agents

Estimates the number of agents needed to meet a service-level target.

Required inputs:

- Forecast call volume
- AHT in seconds
- Interval length in seconds
- Target answer time in seconds
- Target service level
- Shrinkage

Percentages may be entered in any of these forms:

```text
80%
80
0.80
```

Example:

```text
Forecast calls: 100
AHT in seconds: 240
Interval seconds: 3600
Target answer time seconds: 20
Target service level: 80%
Shrinkage: 30%
```

Outputs include:

- Traffic in Erlangs
- Raw agents required
- Scheduled agents after shrinkage
- Achieved service level
- Probability of waiting
- Occupancy
- ASA

### S — Show CDR Summary

Reads a CDR file and displays:

- Total raw rows
- Disposition counts
- Date range
- Number of cleaned answered calls
- Average AHT
- Median AHT
- Maximum included duration

### C — Process a CDR CSV File

Cleans and groups CDR data into time intervals, calculates staffing requirements for each interval, and saves the results to a CSV file.

Required inputs:

- CDR CSV path
- Interval length in minutes
- Target answer time in seconds
- Target service level
- Shrinkage
- Output CSV path

If the output path is left blank, the program writes to:

```text
erlang_c_output.csv
```

## CDR Input Format

The script expects a headerless CDR file with these columns in this exact order:

| Position | Column | Description |
|---:|---|---|
| 1 | `source` | Calling party/source |
| 2 | `destination` | Called party/destination |
| 3 | `call_datetime` | Call date and time |
| 4 | `duration` | Call duration in `HH:MM:SS` format |
| 5 | `disposition` | Call result, such as `Answered` |
| 6 | `unique_id` | Unique call identifier |
| 7 | `caller_id` | Caller ID value |

Example row:

```text
1001,2001,2026-Jan-15 09:30:00 AM,00:03:45,Answered,abc123,+94111234567
```

The expected date format is:

```text
YYYY-Mon-DD HH:MM:SS AM/PM
```

Example:

```text
2026-Jan-15 09:30:00 AM
```

The file reader attempts these formats automatically:

1. UTF-16, tab-separated
2. UTF-8 with BOM, comma-separated
3. Latin-1, comma-separated

## CDR Cleaning Rules

By default, the application:

- Includes only rows with disposition `Answered`
- Rejects invalid dates
- Rejects invalid durations
- Rejects rows missing source or destination
- Includes durations from 1 second through 4 hours
- Excludes rows whose destination is `s`
- Sorts the cleaned rows by call date and time

## Output CSV Columns

The interval forecast contains:

| Column | Description |
|---|---|
| `interval_start` | Start time of the interval |
| `call_volume` | Number of included calls |
| `aht_seconds` | Average handle time |
| `traffic_erlangs` | Offered traffic |
| `raw_agents` | Agents required before shrinkage |
| `scheduled_agents` | Agents required after shrinkage |
| `service_level_percent` | Achieved service level |
| `probability_waiting_percent` | Probability that a call waits |
| `occupancy_percent` | Agent occupancy |
| `asa_seconds` | Average Speed of Answer |

## Using the Module in Python

The functions can also be imported into another Python program.

```python
from calculator import required_agents

result = required_agents(
    call_volume=100,
    aht_seconds=240,
    interval_seconds=3600,
    target_seconds=20,
    target_service_level="80%",
    shrinkage="30%",
)

print(result)
```

Process a CDR file programmatically:

```python
from calculator import process_cdr_for_erlang

forecast = process_cdr_for_erlang(
    file_path="calls.csv",
    interval_minutes=30,
    target_seconds=20,
    target_service_level="80%",
    shrinkage="30%",
    output_path="staffing_forecast.csv",
)

print(forecast.head())
```

## Important Notes

- AHT, interval length, and target answer time must use compatible time units. The interactive application expects seconds for these values unless otherwise stated.
- Shrinkage must be less than 100%.
- Erlang C assumes calls queue until answered and does not directly model abandonment, callbacks, retrials, or blended workloads.
- Forecast accuracy depends on the quality of the call-volume and AHT inputs.
- Intervals with no included calls are omitted from the exported forecast.


