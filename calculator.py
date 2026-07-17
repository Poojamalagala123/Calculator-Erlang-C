import math
import os
import re
from typing import Optional

import pandas as pd
from pyworkforce.queuing import ErlangC


CDR_COLUMNS = [
    "source",
    "destination",
    "call_datetime",
    "duration",
    "disposition",
    "unique_id",
    "caller_id",
]


def hms_to_seconds(value) -> Optional[int]:
    if pd.isna(value):
        return None

    text = str(value).strip()
    parts = text.split(":")

    if len(parts) != 3:
        return None

    try:
        hours, minutes, seconds = [int(part) for part in parts]
        return hours * 3600 + minutes * 60 + seconds
    except ValueError:
        return None


def clean_percent(value) -> float:
    text = str(value).strip().replace("%", "")
    number = float(text)

    if number > 1:
        number = number / 100

    return number


def calculate_aht(total_handle_time_seconds: float, total_answered_calls: int) -> float:
    if total_answered_calls <= 0:
        raise ValueError("Answered calls must be greater than 0.")
    return total_handle_time_seconds / total_answered_calls


def calculate_traffic(call_volume: float, aht_seconds: float, interval_seconds: float) -> float:
    if interval_seconds <= 0:
        raise ValueError("Interval seconds must be greater than 0.")
    return (call_volume * aht_seconds) / interval_seconds


def _erlang_model_from_traffic(
    traffic: float,
    aht_seconds: float,
    target_seconds: float,
) -> ErlangC:
    if aht_seconds <= 0:
        raise ValueError("AHT seconds must be greater than 0.")

    interval_minutes = 60
    aht_minutes = aht_seconds / 60
    transactions = traffic * interval_minutes / aht_minutes

    return ErlangC(
        transactions=transactions,
        aht=aht_minutes,
        asa=target_seconds / 60,
        interval=interval_minutes,
    )


def erlang_c_probability(traffic: float, agents: int) -> float:
    if agents <= traffic:
        return 1.0

    model = _erlang_model_from_traffic(traffic, 60, 0)
    return model.waiting_probability(agents)


def average_speed_of_answer(
    erlang_c: float,
    aht_seconds: float,
    agents: int,
    traffic: float,
) -> float:
    if agents <= traffic:
        return float("inf")
    return erlang_c * aht_seconds / (agents - traffic)


def service_level(
    erlang_c: float,
    agents: int,
    traffic: float,
    target_seconds: float,
    aht_seconds: float,
) -> float:
    if agents <= traffic:
        return 0.0

    model = _erlang_model_from_traffic(traffic, aht_seconds, target_seconds)
    return model.service_level(agents)


def occupancy(traffic: float, agents: int) -> float:
    if agents <= 0:
        return 0.0
    if agents <= traffic:
        return traffic / agents

    model = _erlang_model_from_traffic(traffic, 60, 0)
    return model.achieved_occupancy(agents)


def required_agents(
    call_volume: float,
    aht_seconds: float,
    interval_seconds: float,
    target_seconds: float,
    target_service_level: float,
    shrinkage: float = 0.0,
    max_agents: int = 1000,
) -> dict:
    target_service_level = clean_percent(target_service_level)
    shrinkage = clean_percent(shrinkage)

    if shrinkage >= 1:
        raise ValueError("Shrinkage must be less than 100%.")

    if interval_seconds <= 0:
        raise ValueError("Interval seconds must be greater than 0.")

    model = ErlangC(
        transactions=call_volume,
        aht=aht_seconds / 60,
        asa=target_seconds / 60,
        interval=interval_seconds / 60,
        shrinkage=shrinkage,
    )

    result = model.required_positions(service_level=target_service_level)

    if result["raw_positions"] > max_agents:
        raise RuntimeError("Could not find required agents within max_agents limit.")

    traffic = calculate_traffic(call_volume, aht_seconds, interval_seconds)
    asa = average_speed_of_answer(
        result["waiting_probability"],
        aht_seconds,
        result["raw_positions"],
        traffic,
    )

    return {
        "traffic_erlangs": traffic,
        "raw_agents": result["raw_positions"],
        "scheduled_agents": result["positions"],
        "service_level": result["service_level"],
        "probability_waiting": result["waiting_probability"],
        "occupancy": result["occupancy"],
        "asa_seconds": asa,
    }

def read_cdr_csv(file_path: str) -> pd.DataFrame:
    attempts = [
        {"encoding": "utf-16", "sep": "\t"},
        {"encoding": "utf-8-sig", "sep": ","},
        {"encoding": "latin1", "sep": ","},
    ]

    last_error = None

    for kwargs in attempts:
        try:
            return pd.read_csv(
                file_path,
                header=None,
                names=CDR_COLUMNS,
                engine="python",
                **kwargs,
            )
        except Exception as exc:
            last_error = exc

    raise ValueError(f"Could not read CSV. Last error: {last_error}")


def preprocess_cdr(
    file_path: str,
    min_duration_seconds: int = 1,
    max_duration_seconds: int = 4 * 3600,
    include_dispositions: Optional[list[str]] = None,
) -> pd.DataFrame:
    
    if include_dispositions is None:
        include_dispositions = ["Answered"]

    df = read_cdr_csv(file_path)

    df["call_datetime"] = pd.to_datetime(
        df["call_datetime"],
        format="%Y-%b-%d %I:%M:%S %p",
        errors="coerce",
    )

    df["duration_seconds"] = df["duration"].apply(hms_to_seconds)

    clean = df.copy()

    clean = clean[
        clean["call_datetime"].notna()
        & clean["duration_seconds"].notna()
        & clean["source"].notna()
        & clean["destination"].notna()
    ]

    clean = clean[clean["disposition"].isin(include_dispositions)]

    clean = clean[
        clean["duration_seconds"].between(
            min_duration_seconds,
            max_duration_seconds,
        )
    ]

    clean = clean[clean["destination"].astype(str).str.lower() != "s"]

    clean = clean.sort_values("call_datetime")

    return clean.reset_index(drop=True)


def build_interval_forecast(
    clean_df: pd.DataFrame,
    interval_minutes: int = 60,
) -> pd.DataFrame:
   
    if clean_df.empty:
        raise ValueError("No clean rows available for interval forecast.")

    rule = f"{interval_minutes}min"

    interval_df = (
        clean_df.set_index("call_datetime")
        .resample(rule)
        .agg(
            call_volume=("duration_seconds", "size"),
            total_handle_time_seconds=("duration_seconds", "sum"),
            aht_seconds=("duration_seconds", "mean"),
        )
        .reset_index()
    )

    interval_df = interval_df[interval_df["call_volume"] > 0].copy()

    interval_df["interval_seconds"] = interval_minutes * 60

    return interval_df


def process_cdr_for_erlang(
    file_path: str,
    interval_minutes: int,
    target_seconds: float,
    target_service_level: float,
    shrinkage: float,
    output_path: str = "erlang_c_output.csv",
) -> pd.DataFrame:
    clean_df = preprocess_cdr(file_path)

    interval_df = build_interval_forecast(
        clean_df,
        interval_minutes=interval_minutes,
    )

    results = []

    for _, row in interval_df.iterrows():
        result = required_agents(
            call_volume=row["call_volume"],
            aht_seconds=row["aht_seconds"],
            interval_seconds=row["interval_seconds"],
            target_seconds=target_seconds,
            target_service_level=target_service_level,
            shrinkage=shrinkage,
        )

        results.append(
            {
                "interval_start": row["call_datetime"],
                "call_volume": int(row["call_volume"]),
                "aht_seconds": round(row["aht_seconds"], 2),
                "traffic_erlangs": round(result["traffic_erlangs"], 4),
                "raw_agents": result["raw_agents"],
                "scheduled_agents": result["scheduled_agents"],
                "service_level_percent": round(result["service_level"] * 100, 2),
                "probability_waiting_percent": round(
                    result["probability_waiting"] * 100,
                    2,
                ),
                "occupancy_percent": round(result["occupancy"] * 100, 2),
                "asa_seconds": round(result["asa_seconds"], 2),
            }
        )

    output_df = pd.DataFrame(results)
    output_df.to_csv(output_path, index=False)

    return output_df


def show_cdr_summary(file_path: str) -> None:
    raw = read_cdr_csv(file_path)
    clean = preprocess_cdr(file_path)

    print("\n===== Raw CDR Summary =====")
    print(f"Raw rows: {len(raw)}")
    print("\nDisposition counts:")
    print(raw["disposition"].value_counts(dropna=False).to_string())

    raw["parsed_datetime"] = pd.to_datetime(
        raw["call_datetime"],
        format="%Y-%b-%d %I:%M:%S %p",
        errors="coerce",
    )

    print(f"\nDate range: {raw['parsed_datetime'].min()} to {raw['parsed_datetime'].max()}")

    print("\n===== Cleaned Erlang Input Summary =====")
    print(f"Clean answered rows used for AHT/traffic: {len(clean)}")
    print(f"Average AHT: {clean['duration_seconds'].mean():.2f} seconds")
    print(f"Median AHT: {clean['duration_seconds'].median():.2f} seconds")
    print(f"Max included duration: {clean['duration_seconds'].max():.2f} seconds")


def menu() -> None:
    while True:
        print("\n===== Erlang C Calculator =====")
        print("A - Calculate AHT")
        print("T - Calculate Traffic / Offered Load")
        print("E - Calculate Erlang C outputs")
        print("R - Calculate Required Agents")
        print("S - Show CDR summary")
        print("C - Process CDR CSV file")
        print("Q - Quit")

        choice = input("Enter choice: ").strip().upper()

        try:
            if choice == "A":
                total_time = float(input("Total handle time in seconds: "))
                calls = int(input("Total answered calls: "))
                print(f"AHT = {calculate_aht(total_time, calls):.2f} seconds")

            elif choice == "T":
                calls = float(input("Call volume: "))
                aht = float(input("AHT in seconds: "))
                interval = float(input("Interval in seconds: "))
                print(
                    f"Traffic = {calculate_traffic(calls, aht, interval):.4f} Erlangs"
                )

            elif choice == "E":
                traffic = float(input("Traffic in Erlangs: "))
                agents = int(input("Agents: "))
                aht = float(input("AHT in seconds: "))
                target = float(input("Target answer time in seconds: "))

                ec = erlang_c_probability(traffic, agents)
                asa = average_speed_of_answer(ec, aht, agents, traffic)
                sl = service_level(ec, agents, traffic, target, aht)
                occ = occupancy(traffic, agents)

                print(f"Probability of waiting = {ec * 100:.2f}%")
                print(f"ASA = {asa:.2f} seconds")
                print(f"Service level = {sl * 100:.2f}%")
                print(f"Occupancy = {occ * 100:.2f}%")

            elif choice == "R":
                calls = float(input("Forecast calls: "))
                aht = float(input("AHT in seconds: "))
                interval = float(input("Interval seconds: "))
                target = float(input("Target answer time seconds: "))
                target_sl = input("Target service level, e.g. 80%, 80, or 0.80: ")
                shrinkage = input("Shrinkage, e.g. 30%, 30, or 0.30: ")

                result = required_agents(
                    calls,
                    aht,
                    interval,
                    target,
                    target_sl,
                    shrinkage,
                )

                print(f"Traffic = {result['traffic_erlangs']:.4f} Erlangs")
                print(f"Raw agents = {result['raw_agents']}")
                print(f"Scheduled agents = {result['scheduled_agents']}")
                print(f"Service level = {result['service_level'] * 100:.2f}%")
                print(f"Probability waiting = {result['probability_waiting'] * 100:.2f}%")
                print(f"Occupancy = {result['occupancy'] * 100:.2f}%")
                print(f"ASA = {result['asa_seconds']:.2f} seconds")

            elif choice == "S":
                file_path = input("CDR CSV path: ").strip()
                show_cdr_summary(file_path)

            elif choice == "C":
                file_path = input("CDR CSV path: ").strip()
                interval_minutes = int(input("Interval minutes, e.g. 15, 30, 60: "))
                target_seconds = float(input("Target answer time seconds, e.g. 20: "))
                target_sl = input("Target service level, e.g. 80%, 80, or 0.80: ")
                shrinkage = input("Shrinkage, e.g. 30%, 30, or 0.30: ")
                output_path = input(
                    "Output CSV path [default erlang_c_output.csv]: "
                ).strip()

                if not output_path:
                    output_path = "erlang_c_output.csv"

                output_df = process_cdr_for_erlang(
                    file_path=file_path,
                    interval_minutes=interval_minutes,
                    target_seconds=target_seconds,
                    target_service_level=target_sl,
                    shrinkage=shrinkage,
                    output_path=output_path,
                )

                print(f"\nDone. Output saved to: {output_path}")
                print(output_df.head(20).to_string(index=False))

            elif choice == "Q":
                print("Goodbye.")
                break

            else:
                print("Invalid choice.")

        except Exception as exc:
            print(f"Error: {exc}")


if __name__ == "__main__":
    menu()
