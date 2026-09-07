from __future__ import annotations
import pandas as pd

def validate_agent_rest_period(
    schedule: pd.DataFrame,
    agent_id: str,
    target_date: str,
    minimum_rest_hours: int = 8,
) -> dict:
   
    if schedule.empty:
        raise ValueError("Schedule is empty.")

    frame = schedule.copy()

    frame["date"] = pd.to_datetime(
        frame["date"]
    )

    target_date = pd.Timestamp(
        target_date
    )

    agent_rows = frame.loc[
        frame["agent_id"].astype(str)
        == str(agent_id)
    ].sort_values("date")

    target_rows = agent_rows.loc[
        agent_rows["date"] == target_date
    ]

    if target_rows.empty:
        raise ValueError(
            f"No schedule found for {agent_id} on "
            f"{target_date.strftime('%Y-%m-%d')}."
        )

    target_row = target_rows.iloc[0]

    if str(target_row["status"]) != "WORK":
        return {
            "valid": True,
            "message": "Agent is not working on the target date.",
        }

    shift_lookup = {
        "NIGHT": {
            "start": 0,
            "end": 8,
        },
        "MORNING": {
            "start": 8,
            "end": 16,
        },
        "EVENING": {
            "start": 16,
            "end": 24,
        },
    }

    target_shift_code = str(
        target_row["shift_code"]
    )

    if target_shift_code not in shift_lookup:
        return {
            "valid": True,
            "message": "No rest validation required.",
        }

    target_start = (
        target_date
        + pd.Timedelta(
            hours=shift_lookup[target_shift_code]["start"]
        )
    )

    target_end = (
        target_date
        + pd.Timedelta(
            hours=shift_lookup[target_shift_code]["end"]
        )
    )

    previous_date = (
        target_date - pd.Timedelta(days=1)
    )

    next_date = (
        target_date + pd.Timedelta(days=1)
    )

    # ---------------------------------------------
    # Check previous day
    # ---------------------------------------------

    previous_rows = agent_rows.loc[
        agent_rows["date"] == previous_date
    ]

    if not previous_rows.empty:

        previous_row = previous_rows.iloc[0]

        previous_status = str(
            previous_row["status"]
        )

        previous_shift_code = str(
            previous_row["shift_code"]
        )

        if (
            previous_status == "WORK"
            and previous_shift_code in shift_lookup
        ):

            previous_end = (
                previous_date
                + pd.Timedelta(
                    hours=shift_lookup[
                        previous_shift_code
                    ]["end"]
                )
            )

            rest_hours = (
                target_start - previous_end
            ).total_seconds() / 3600

            if rest_hours < minimum_rest_hours:

                return {
                    "valid": False,
                    "reason": "PREVIOUS_SHIFT_REST",
                    "rest_hours": rest_hours,
                    "message": (
                        f"{agent_id} would only have "
                        f"{rest_hours:.1f} hours rest "
                        f"before the new shift."
                    ),
                }

    # ---------------------------------------------
    # Check next day
    # ---------------------------------------------

    next_rows = agent_rows.loc[
        agent_rows["date"] == next_date
    ]

    if not next_rows.empty:

        next_row = next_rows.iloc[0]

        next_status = str(
            next_row["status"]
        )

        next_shift_code = str(
            next_row["shift_code"]
        )

        if (
            next_status == "WORK"
            and next_shift_code in shift_lookup
        ):

            next_start = (
                next_date
                + pd.Timedelta(
                    hours=shift_lookup[
                        next_shift_code
                    ]["start"]
                )
            )

            rest_hours = (
                next_start - target_end
            ).total_seconds() / 3600

            if rest_hours < minimum_rest_hours:

                return {
                    "valid": False,
                    "reason": "NEXT_SHIFT_REST",
                    "rest_hours": rest_hours,
                    "message": (
                        f"{agent_id} would only have "
                        f"{rest_hours:.1f} hours rest "
                        f"before the next shift."
                    ),
                }

    return {
        "valid": True,
        "message": (
            f"{agent_id} satisfies the minimum "
            f"{minimum_rest_hours}-hour rest rule."
        ),
    }

def swap_agent_shifts(
    schedule: pd.DataFrame,
    agent_1: str,
    agent_2: str,
    swap_date: str,
) -> tuple[pd.DataFrame, dict]:
    
    if schedule.empty:
        raise ValueError("Schedule is empty.")

    updated_schedule = schedule.copy()

    agent_1 = str(agent_1).strip()
    agent_2 = str(agent_2).strip()

    if agent_1 == agent_2:
        raise ValueError(
            "Select two different agents for a shift swap."
        )

    swap_date = pd.Timestamp(
        swap_date
    ).strftime("%Y-%m-%d")

    # ---------------------------------------------
    # Find Agent 1 schedule row
    # ---------------------------------------------

    agent_1_rows = updated_schedule.loc[
        (
            updated_schedule["agent_id"].astype(str)
            == agent_1
        )
        & (
            updated_schedule["date"].astype(str)
            == swap_date
        )
    ]

    if agent_1_rows.empty:
        raise ValueError(
            f"No schedule found for {agent_1} on {swap_date}."
        )

    if len(agent_1_rows) > 1:
        raise ValueError(
            f"Multiple schedule rows found for "
            f"{agent_1} on {swap_date}."
        )

    # ---------------------------------------------
    # Find Agent 2 schedule row
    # ---------------------------------------------

    agent_2_rows = updated_schedule.loc[
        (
            updated_schedule["agent_id"].astype(str)
            == agent_2
        )
        & (
            updated_schedule["date"].astype(str)
            == swap_date
        )
    ]

    if agent_2_rows.empty:
        raise ValueError(
            f"No schedule found for {agent_2} on {swap_date}."
        )

    if len(agent_2_rows) > 1:
        raise ValueError(
            f"Multiple schedule rows found for "
            f"{agent_2} on {swap_date}."
        )

    index_1 = agent_1_rows.index[0]
    index_2 = agent_2_rows.index[0]

    # ---------------------------------------------
    # Read current assignments
    # ---------------------------------------------

    status_1 = str(
        updated_schedule.at[index_1, "status"]
    )

    status_2 = str(
        updated_schedule.at[index_2, "status"]
    )

    shift_code_1 = str(
        updated_schedule.at[index_1, "shift_code"]
    )

    shift_code_2 = str(
        updated_schedule.at[index_2, "shift_code"]
    )

    shift_1 = str(
        updated_schedule.at[index_1, "shift"]
    )

    shift_2 = str(
        updated_schedule.at[index_2, "shift"]
    )

    # ---------------------------------------------
    # Basic validation
    # ---------------------------------------------

    if status_1 != "WORK":
        raise ValueError(
            f"{agent_1} is not working on {swap_date}."
        )

    if status_2 != "WORK":
        raise ValueError(
            f"{agent_2} is not working on {swap_date}."
        )

    if shift_code_1 in {"OFF", "LEAVE"}:
        raise ValueError(
            f"{agent_1} cannot swap from {shift_code_1}."
        )

    if shift_code_2 in {"OFF", "LEAVE"}:
        raise ValueError(
            f"{agent_2} cannot swap from {shift_code_2}."
        )

    if shift_code_1 == shift_code_2:
        raise ValueError(
            "Both agents already have the same shift."
        )

    # ---------------------------------------------
    # Create audit columns if needed
    # ---------------------------------------------

    if "previous_shift_code" not in updated_schedule.columns:
        updated_schedule["previous_shift_code"] = None

    if "previous_shift" not in updated_schedule.columns:
        updated_schedule["previous_shift"] = None

    if "assignment_type" not in updated_schedule.columns:
        updated_schedule["assignment_type"] = None

    # ---------------------------------------------
    # Store original assignments
    # ---------------------------------------------

    updated_schedule.at[
        index_1,
        "previous_shift_code",
    ] = shift_code_1

    updated_schedule.at[
        index_1,
        "previous_shift",
    ] = shift_1

    updated_schedule.at[
        index_2,
        "previous_shift_code",
    ] = shift_code_2

    updated_schedule.at[
        index_2,
        "previous_shift",
    ] = shift_2

    # ---------------------------------------------
    # Perform swap
    # ---------------------------------------------

    updated_schedule.at[
        index_1,
        "shift_code",
    ] = shift_code_2

    updated_schedule.at[
        index_1,
        "shift",
    ] = shift_2

    updated_schedule.at[
        index_2,
        "shift_code",
    ] = shift_code_1

    updated_schedule.at[
        index_2,
        "shift",
    ] = shift_1

    updated_schedule.at[
        index_1,
        "assignment_type",
    ] = "SHIFT_SWAP"

    updated_schedule.at[
        index_2,
        "assignment_type",
    ] = "SHIFT_SWAP"

    # ---------------------------------------------
    # Validate rest period after swap
    # ---------------------------------------------

    validation_1 = validate_agent_rest_period(
        updated_schedule,
        agent_1,
        swap_date,
    )

    validation_2 = validate_agent_rest_period(
        updated_schedule,
        agent_2,
        swap_date,
    )

    if not validation_1["valid"]:
        raise ValueError(
            validation_1["message"]
        )

    if not validation_2["valid"]:
        raise ValueError(
            validation_2["message"]
        )

    # ---------------------------------------------
    # Build result
    # ---------------------------------------------

    result = {
        "swap_applied": True,
        "date": swap_date,

        "agent_1": agent_1,
        "agent_1_previous_shift": shift_code_1,
        "agent_1_new_shift": shift_code_2,

        "agent_2": agent_2,
        "agent_2_previous_shift": shift_code_2,
        "agent_2_new_shift": shift_code_1,

        "agent_1_rest_validation": validation_1,
        "agent_2_rest_validation": validation_2,

        "message": (
            f"{agent_1} and {agent_2} successfully "
            f"swapped shifts on {swap_date}."
        ),
    }

    return updated_schedule, result

