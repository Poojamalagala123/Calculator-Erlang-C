from __future__ import annotations
from typing import Optional
import pandas as pd
from app.core.constants import SHIFT_DEFINITIONS
from app.core.scheduling.shifts import build_shift_requirements
from app.core.scheduling.swap import validate_agent_rest_period

def mark_agent_leave(
    schedule: pd.DataFrame,
    agent_id: str,
    leave_date: str,
) -> tuple[pd.DataFrame, dict]:
    
    if schedule.empty:
        raise ValueError("Schedule is empty.")

    required_columns = {
        "agent_id",
        "date",
        "shift_code",
        "shift",
        "status",
    }

    missing_columns = required_columns - set(schedule.columns)

    if missing_columns:
        raise ValueError(
            f"Schedule is missing required columns: {sorted(missing_columns)}"
        )

    schedule = schedule.copy()

    agent_id = str(agent_id).strip()

    leave_date = pd.Timestamp(leave_date).strftime("%Y-%m-%d")

    matching_rows = schedule.loc[
        (schedule["agent_id"].astype(str) == agent_id)
        & (schedule["date"].astype(str) == leave_date)
    ]

    if matching_rows.empty:
        raise ValueError(
            f"No schedule found for agent {agent_id} on {leave_date}."
        )

    if len(matching_rows) > 1:
        raise ValueError(
            f"Multiple schedule rows found for agent {agent_id} on {leave_date}."
        )

    row_index = matching_rows.index[0]

    current_shift_code = str(
        schedule.at[row_index, "shift_code"]
    )

    current_shift = str(
        schedule.at[row_index, "shift"]
    )

    current_status = str(
        schedule.at[row_index, "status"]
    )

    # ---------------------------------------------
    # Agent is already OFF
    # ---------------------------------------------

    if current_shift_code == "OFF" or current_status == "OFF":

        return schedule, {
            "agent_id": agent_id,
            "leave_date": leave_date,
            "leave_required": False,
            "original_shift_code": "OFF",
            "original_shift": "OFF",
            "message": "Agent is already OFF on the selected date.",
        }

    # ---------------------------------------------
    # Agent is already on leave
    # ---------------------------------------------

    if current_shift_code == "LEAVE" or current_status == "LEAVE":

        return schedule, {
            "agent_id": agent_id,
            "leave_date": leave_date,
            "leave_required": False,
            "original_shift_code": schedule.at[
                row_index,
                "original_shift_code",
            ]
            if "original_shift_code" in schedule.columns
            else None,
            "original_shift": schedule.at[
                row_index,
                "original_shift",
            ]
            if "original_shift" in schedule.columns
            else None,
            "message": "Agent is already marked as LEAVE.",
        }

    # ---------------------------------------------
    # Create columns if they don't exist yet
    # ---------------------------------------------

    if "original_shift_code" not in schedule.columns:
        schedule["original_shift_code"] = None

    if "original_shift" not in schedule.columns:
        schedule["original_shift"] = None

    # ---------------------------------------------
    # Save original shift before changing it
    # ---------------------------------------------

    schedule.at[
        row_index,
        "original_shift_code",
    ] = current_shift_code

    schedule.at[
        row_index,
        "original_shift",
    ] = current_shift

    # ---------------------------------------------
    # Mark agent as LEAVE
    # ---------------------------------------------

    schedule.at[
        row_index,
        "shift_code",
    ] = "LEAVE"

    schedule.at[
        row_index,
        "shift",
    ] = "LEAVE"

    schedule.at[
        row_index,
        "status",
    ] = "LEAVE"

    result = {
        "agent_id": agent_id,
        "leave_date": leave_date,
        "leave_required": True,
        "original_shift_code": current_shift_code,
        "original_shift": current_shift,
        "message": (
            f"{agent_id} marked as LEAVE on {leave_date}. "
            f"Original shift was {current_shift}."
        ),
    }

    return schedule, result

def calculate_leave_coverage(
    schedule: pd.DataFrame,
    shift_requirements: pd.DataFrame,
    leave_result: dict,
) -> dict:
    
    if schedule.empty:
        raise ValueError("Schedule is empty.")

    if shift_requirements.empty:
        raise ValueError("Shift requirements are empty.")

    if not leave_result.get("leave_required"):
        return {
            "coverage_required": False,
            "shortage": 0,
            "message": leave_result.get(
                "message",
                "No replacement is required.",
            ),
        }

    agent_id = leave_result["agent_id"]
    leave_date = leave_result["leave_date"]
    original_shift_code = leave_result["original_shift_code"]

    # ---------------------------------------------
    # Find required staffing for this date + shift
    # ---------------------------------------------

    requirements = shift_requirements.copy()

    requirements["date"] = pd.to_datetime(
        requirements["date"]
    ).dt.strftime("%Y-%m-%d")

    requirement_row = requirements.loc[
        (requirements["date"] == leave_date)
        & (
            requirements["shift_code"].astype(str)
            == str(original_shift_code)
        )
    ]

    if requirement_row.empty:
        raise ValueError(
            f"No staffing requirement found for "
            f"{leave_date} / {original_shift_code}."
        )

    required_agents = int(
        requirement_row.iloc[0]["required_agents"]
    )

    # ---------------------------------------------
    # Count agents currently working this shift
    # ---------------------------------------------

    current_schedule = schedule.copy()

    current_schedule["date"] = (
        current_schedule["date"]
        .astype(str)
    )

    working_agents = current_schedule.loc[
        (current_schedule["date"] == leave_date)
        & (
            current_schedule["shift_code"].astype(str)
            == str(original_shift_code)
        )
        & (
            current_schedule["status"].astype(str)
            == "WORK"
        )
    ]

    assigned_agents = len(working_agents)

    # ---------------------------------------------
    # Calculate shortage
    # ---------------------------------------------

    shortage = max(
        required_agents - assigned_agents,
        0,
    )

    coverage_ok = assigned_agents >= required_agents

    result = {
        "agent_id": agent_id,
        "leave_date": leave_date,
        "shift_code": original_shift_code,
        "required_agents": required_agents,
        "assigned_agents": assigned_agents,
        "shortage": shortage,
        "coverage_ok": coverage_ok,
    }

    if coverage_ok:
        result["message"] = (
            f"Coverage is still sufficient for "
            f"{original_shift_code} on {leave_date}. "
            f"Required: {required_agents}, "
            f"Assigned: {assigned_agents}."
        )
    else:
        result["message"] = (
            f"Replacement required for "
            f"{original_shift_code} on {leave_date}. "
            f"Required: {required_agents}, "
            f"Assigned: {assigned_agents}, "
            f"Shortage: {shortage}."
        )

    return result

def find_leave_replacement_candidates(
    schedule: pd.DataFrame,
    leave_result: dict,
    max_working_days_per_week: int = 5,
) -> list[dict]:
    
    if schedule.empty:
        raise ValueError("Schedule is empty.")

    if not leave_result.get("leave_required"):
        return []

    leave_agent = str(
        leave_result["agent_id"]
    )

    leave_date = pd.Timestamp(
        leave_result["leave_date"]
    )

    leave_date_text = leave_date.strftime(
        "%Y-%m-%d"
    )

    target_shift_code = str(
        leave_result["original_shift_code"]
    )

    target_shift = str(
        leave_result["original_shift"]
    )

    frame = schedule.copy()

    frame["date"] = pd.to_datetime(
        frame["date"]
    )

    # -------------------------------------------------
    # Calculate Monday-Sunday leave week
    # -------------------------------------------------

    week_start = (
        leave_date
        - pd.Timedelta(
            days=leave_date.weekday()
        )
    )

    week_end = (
        week_start
        + pd.Timedelta(days=6)
    )

    # -------------------------------------------------
    # Get all agents except leave agent
    # -------------------------------------------------

    agents = sorted(
        agent
        for agent in frame["agent_id"]
        .astype(str)
        .unique()
        if agent != leave_agent
    )

    candidates = []

    for agent in agents:

        agent_rows = frame.loc[
            frame["agent_id"]
            .astype(str)
            == agent
        ]

        # ---------------------------------------------
        # Find schedule on leave date
        # ---------------------------------------------

        day_rows = agent_rows.loc[
            agent_rows["date"]
            == leave_date
        ]

        if day_rows.empty:
            continue

        if len(day_rows) > 1:
            continue

        day_row = day_rows.iloc[0]

        current_status = str(
            day_row["status"]
        )

        current_shift_code = str(
            day_row["shift_code"]
        )

        # ---------------------------------------------
        # Only OFF agents can be selected
        # ---------------------------------------------

        if (
            current_status != "OFF"
            or current_shift_code != "OFF"
        ):
            continue

        # ---------------------------------------------
        # Count unique working days in leave week
        # ---------------------------------------------

        weekly_rows = agent_rows.loc[
            (
                agent_rows["date"]
                >= week_start
            )
            & (
                agent_rows["date"]
                <= week_end
            )
            & (
                agent_rows["status"]
                .astype(str)
                == "WORK"
            )
        ]

        weekly_working_days = int(
            weekly_rows["date"].nunique()
        )

        # ---------------------------------------------
        # Calculate working days after cover
        # ---------------------------------------------

        weekly_days_after_cover = (
            weekly_working_days + 1
        )

        # ---------------------------------------------
        # Reject if cover would exceed 5 days
        # ---------------------------------------------

        if (
            weekly_days_after_cover
            > max_working_days_per_week
        ):
            continue

        # ---------------------------------------------
        # Count unique monthly working days
        # ---------------------------------------------

        monthly_working_days = int(
            agent_rows.loc[
                agent_rows["status"]
                .astype(str)
                == "WORK",
                "date",
            ].nunique()
        )

        # ---------------------------------------------
        # Add valid candidate
        # ---------------------------------------------

        candidates.append(
            {
                "agent_id": agent,
                "leave_date": leave_date_text,

                "target_shift_code":
                    target_shift_code,

                "target_shift":
                    target_shift,

                "current_status": "OFF",

                "weekly_working_days":
                    weekly_working_days,

                "weekly_days_after_cover":
                    weekly_days_after_cover,

                "monthly_working_days":
                    monthly_working_days,
            }
        )

    # -------------------------------------------------
    # Rank candidates fairly
    # -------------------------------------------------

    candidates.sort(
        key=lambda item: (
            item[
                "weekly_working_days"
            ],
            item[
                "monthly_working_days"
            ],
            item[
                "agent_id"
            ],
        )
    )

    return candidates

def assign_leave_replacement(
    schedule: pd.DataFrame,
    leave_result: dict,
    coverage_result: dict,
    candidates: list[dict],
) -> tuple[pd.DataFrame, dict]:
   
    if schedule.empty:
        raise ValueError("Schedule is empty.")

    if not leave_result.get("leave_required"):
        return schedule, {
            "replacement_applied": False,
            "message": "No leave replacement is required.",
        }

    if coverage_result.get("coverage_ok"):
        return schedule, {
            "replacement_applied": False,
            "message": (
                "Coverage is already sufficient. "
                "No replacement is required."
            ),
        }

    if not candidates:
        return schedule, {
            "replacement_applied": False,
            "message": (
                "No eligible OFF agent was found "
                "to cover the leave shift."
            ),
        }

    updated_schedule = schedule.copy()

    replacement = candidates[0]

    replacement_agent = replacement["agent_id"]
    leave_date = leave_result["leave_date"]

    target_shift_code = leave_result[
        "original_shift_code"
    ]

    target_shift = leave_result[
        "original_shift"
    ]

    # -------------------------------------------------
    # Find replacement agent row
    # -------------------------------------------------

    matching_rows = updated_schedule.loc[
        (
            updated_schedule["agent_id"].astype(str)
            == str(replacement_agent)
        )
        & (
            updated_schedule["date"].astype(str)
            == str(leave_date)
        )
    ]

    if matching_rows.empty:
        raise ValueError(
            f"No schedule row found for replacement agent "
            f"{replacement_agent} on {leave_date}."
        )

    if len(matching_rows) > 1:
        raise ValueError(
            f"Multiple schedule rows found for replacement agent "
            f"{replacement_agent} on {leave_date}."
        )

    row_index = matching_rows.index[0]

    current_status = str(
        updated_schedule.at[
            row_index,
            "status",
        ]
    )

    current_shift_code = str(
        updated_schedule.at[
            row_index,
            "shift_code",
        ]
    )

    # -------------------------------------------------
    # Confirm agent is still OFF
    # -------------------------------------------------

    if (
        current_status != "OFF"
        or current_shift_code != "OFF"
    ):
        raise ValueError(
            f"{replacement_agent} is no longer "
            f"OFF on {leave_date}."
        )

    # -------------------------------------------------
    # Keep previous assignment for audit/history
    # -------------------------------------------------

    if (
        "previous_shift_code"
        not in updated_schedule.columns
    ):
        updated_schedule[
            "previous_shift_code"
        ] = None

    if (
        "previous_shift"
        not in updated_schedule.columns
    ):
        updated_schedule[
            "previous_shift"
        ] = None

    if (
        "assignment_type"
        not in updated_schedule.columns
    ):
        updated_schedule[
            "assignment_type"
        ] = None

    updated_schedule.at[
        row_index,
        "previous_shift_code",
    ] = current_shift_code

    updated_schedule.at[
        row_index,
        "previous_shift",
    ] = "OFF"

    # -------------------------------------------------
    # Assign replacement shift
    # -------------------------------------------------

    updated_schedule.at[
        row_index,
        "shift_code",
    ] = target_shift_code

    updated_schedule.at[
        row_index,
        "shift",
    ] = target_shift

    updated_schedule.at[
        row_index,
        "status",
    ] = "WORK"

    updated_schedule.at[
        row_index,
        "assignment_type",
    ] = "LEAVE_COVER"

    # -------------------------------------------------
    # Validate rest period after assignment
    # -------------------------------------------------

    rest_validation = validate_agent_rest_period(
        updated_schedule,
        replacement_agent,
        leave_date,
    )

    if not rest_validation["valid"]:
        raise ValueError(
            rest_validation["message"]
        )

    # -------------------------------------------------
    # Build result
    # -------------------------------------------------

    result = {
        "replacement_applied": True,
        "leave_agent": leave_result[
            "agent_id"
        ],
        "replacement_agent": replacement_agent,
        "leave_date": leave_date,
        "shift_code": target_shift_code,
        "shift": target_shift,

        "weekly_working_days_before":
            replacement[
                "weekly_working_days"
            ],

        "weekly_working_days_after":
            replacement[
                "weekly_days_after_cover"
            ],

        "monthly_working_days_before":
            replacement[
                "monthly_working_days"
            ],

        "rest_validation":
            rest_validation,

        "message": (
            f"{replacement_agent} assigned to cover "
            f"{target_shift_code} on {leave_date} "
            f"for {leave_result['agent_id']}."
        ),
    }

    return updated_schedule, result

def find_safe_shift_transfer_candidates(
    schedule: pd.DataFrame,
    shift_requirements: pd.DataFrame,
    leave_result: dict,
) -> list[dict]:
   
    if schedule.empty:
        raise ValueError("Schedule is empty.")

    if shift_requirements.empty:
        raise ValueError("Shift requirements are empty.")

    if not leave_result.get("leave_required"):
        return []

    frame = schedule.copy()

    frame["date"] = pd.to_datetime(
        frame["date"]
    ).dt.strftime("%Y-%m-%d")

    requirements = shift_requirements.copy()

    requirements["date"] = pd.to_datetime(
        requirements["date"]
    ).dt.strftime("%Y-%m-%d")

    leave_date = leave_result["leave_date"]
    leave_agent = leave_result["agent_id"]

    target_shift_code = leave_result[
        "original_shift_code"
    ]

    target_shift = leave_result[
        "original_shift"
    ]

    # ---------------------------------------------
    # Agents working on the leave date
    # but not already in the target shift
    # ---------------------------------------------

    working_rows = frame.loc[
        (frame["date"] == leave_date)
        & (frame["status"].astype(str) == "WORK")
        & (
            frame["shift_code"].astype(str)
            != str(target_shift_code)
        )
        & (
            frame["agent_id"].astype(str)
            != str(leave_agent)
        )
    ]

    candidates = []

    for row in working_rows.itertuples(index=False):

        source_shift_code = str(
            row.shift_code
        )

        source_shift = str(
            row.shift
        )

        # -----------------------------------------
        # Required staffing in source shift
        # -----------------------------------------

        source_requirement = requirements.loc[
            (requirements["date"] == leave_date)
            & (
                requirements["shift_code"].astype(str)
                == source_shift_code
            )
        ]

        if source_requirement.empty:
            continue

        required_agents = int(
            source_requirement.iloc[0][
                "required_agents"
            ]
        )

        # -----------------------------------------
        # Current agents in source shift
        # -----------------------------------------

        source_working = frame.loc[
            (frame["date"] == leave_date)
            & (
                frame["shift_code"].astype(str)
                == source_shift_code
            )
            & (
                frame["status"].astype(str)
                == "WORK"
            )
        ]

        assigned_agents = len(
            source_working
        )

        assigned_after_move = (
            assigned_agents - 1
        )

        spare_agents = (
            assigned_agents - required_agents
        )

        # -----------------------------------------
        # Reject if moving this agent creates
        # a shortage in their original shift
        # -----------------------------------------

        if assigned_after_move < required_agents:
            continue

        # -----------------------------------------
        # Monthly working days
        # -----------------------------------------

        agent_rows = frame.loc[
            frame["agent_id"].astype(str)
            == str(row.agent_id)
        ]

        monthly_working_days = len(
            agent_rows.loc[
                agent_rows["status"].astype(str)
                == "WORK"
            ]
        )

        candidates.append(
            {
                "agent_id": row.agent_id,
                "leave_date": leave_date,
                "target_shift_code": target_shift_code,
                "target_shift": target_shift,
                "source_shift_code": source_shift_code,
                "source_shift": source_shift,
                "source_required_agents": required_agents,
                "source_assigned_agents": assigned_agents,
                "source_assigned_after_move": assigned_after_move,
                "source_spare_agents": spare_agents,
                "monthly_working_days": monthly_working_days,
            }
        )

    # ---------------------------------------------
    # Ranking:
    # 1. shifts with more spare agents
    # 2. agents with fewer monthly working days
    # 3. agent id
    # ---------------------------------------------

    candidates.sort(
        key=lambda item: (
            -item["source_spare_agents"],
            item["monthly_working_days"],
            item["agent_id"],
        )
    )

    return candidates

def assign_safe_shift_transfer(
    schedule: pd.DataFrame,
    leave_result: dict,
    transfer_candidates: list[dict],
) -> tuple[pd.DataFrame, dict]:
    
    if schedule.empty:
        raise ValueError("Schedule is empty.")

    if not transfer_candidates:
        return schedule, {
            "transfer_applied": False,
            "message": (
                "No safe shift-transfer candidate "
                "was found."
            ),
        }

    updated_schedule = schedule.copy()

    candidate = transfer_candidates[0]

    agent_id = candidate["agent_id"]
    leave_date = leave_result["leave_date"]

    # -------------------------------------------------
    # Find transfer agent row
    # -------------------------------------------------

    row = updated_schedule.loc[
        (
            updated_schedule["agent_id"]
            .astype(str)
            == str(agent_id)
        )
        & (
            updated_schedule["date"]
            .astype(str)
            == str(leave_date)
        )
    ]

    if row.empty:
        raise ValueError(
            f"No schedule found for {agent_id} "
            f"on {leave_date}."
        )

    if len(row) > 1:
        raise ValueError(
            f"Multiple schedule rows found for "
            f"{agent_id} on {leave_date}."
        )

    row_index = row.index[0]

    # -------------------------------------------------
    # Audit columns
    # -------------------------------------------------

    if (
        "previous_shift_code"
        not in updated_schedule.columns
    ):
        updated_schedule[
            "previous_shift_code"
        ] = None

    if (
        "previous_shift"
        not in updated_schedule.columns
    ):
        updated_schedule[
            "previous_shift"
        ] = None

    if (
        "assignment_type"
        not in updated_schedule.columns
    ):
        updated_schedule[
            "assignment_type"
        ] = None

    # -------------------------------------------------
    # Store original shift
    # -------------------------------------------------

    updated_schedule.at[
        row_index,
        "previous_shift_code",
    ] = candidate["source_shift_code"]

    updated_schedule.at[
        row_index,
        "previous_shift",
    ] = candidate["source_shift"]

    # -------------------------------------------------
    # Move agent to leave shift
    # -------------------------------------------------

    updated_schedule.at[
        row_index,
        "shift_code",
    ] = leave_result[
        "original_shift_code"
    ]

    updated_schedule.at[
        row_index,
        "shift",
    ] = leave_result[
        "original_shift"
    ]

    updated_schedule.at[
        row_index,
        "status",
    ] = "WORK"

    updated_schedule.at[
        row_index,
        "assignment_type",
    ] = "SHIFT_TRANSFER"

    # -------------------------------------------------
    # Validate rest period after transfer
    # -------------------------------------------------

    rest_validation = validate_agent_rest_period(
        updated_schedule,
        agent_id,
        leave_date,
    )

    if not rest_validation["valid"]:
        raise ValueError(
            rest_validation["message"]
        )

    # -------------------------------------------------
    # Build result
    # -------------------------------------------------

    result = {
        "transfer_applied": True,

        "leave_agent":
            leave_result["agent_id"],

        "replacement_agent":
            agent_id,

        "leave_date":
            leave_date,

        "source_shift_code":
            candidate[
                "source_shift_code"
            ],

        "target_shift_code":
            leave_result[
                "original_shift_code"
            ],

        "source_required_agents":
            candidate[
                "source_required_agents"
            ],

        "source_assigned_after_move":
            candidate[
                "source_assigned_after_move"
            ],

        "rest_validation":
            rest_validation,

        "message": (
            f"{agent_id} moved from "
            f"{candidate['source_shift_code']} "
            f"to {leave_result['original_shift_code']} "
            f"on {leave_date}."
        ),
    }

    return updated_schedule, result

def resolve_agent_leave(
    schedule: pd.DataFrame,
    shift_requirements: pd.DataFrame,
    agent_id: str,
    leave_date: str,
) -> tuple[pd.DataFrame, dict]:
    
    if schedule.empty:
        raise ValueError("Schedule is empty.")

    if shift_requirements.empty:
        raise ValueError("Shift requirements are empty.")

    # -------------------------------------------------
    # Step 1: Mark requested agent as leave
    # -------------------------------------------------

    updated_schedule, leave_result = mark_agent_leave(
        schedule=schedule,
        agent_id=agent_id,
        leave_date=leave_date,
    )

    # -------------------------------------------------
    # Step 2: Check coverage after leave
    # -------------------------------------------------

    coverage_before = calculate_leave_coverage(
        schedule=updated_schedule,
        shift_requirements=shift_requirements,
        leave_result=leave_result,
    )

    # -------------------------------------------------
    # If agent was already OFF / no leave needed
    # -------------------------------------------------

    if not leave_result.get("leave_required"):

        return updated_schedule, {
            "resolved": True,
            "method": "NO_ACTION",
            "leave": leave_result,
            "coverage_before": coverage_before,
            "coverage_after": coverage_before,
            "message": leave_result.get(
                "message",
                "No action required.",
            ),
        }

    # -------------------------------------------------
    # If coverage is still okay after leave
    # -------------------------------------------------

    if coverage_before.get("coverage_ok"):

        return updated_schedule, {
            "resolved": True,
            "method": "NO_REPLACEMENT_REQUIRED",
            "leave": leave_result,
            "coverage_before": coverage_before,
            "coverage_after": coverage_before,
            "message": (
                "Leave approved. Existing staffing "
                "is still sufficient."
            ),
        }

    # -------------------------------------------------
    # Step 3: Try OFF-agent replacement
    # -------------------------------------------------

    off_candidates = find_leave_replacement_candidates(
        schedule=updated_schedule,
        leave_result=leave_result,
    )

    if off_candidates:

        updated_schedule, replacement_result = (
            assign_leave_replacement(
                schedule=updated_schedule,
                leave_result=leave_result,
                coverage_result=coverage_before,
                candidates=off_candidates,
            )
        )

        coverage_after = calculate_leave_coverage(
            schedule=updated_schedule,
            shift_requirements=shift_requirements,
            leave_result=leave_result,
        )

        if coverage_after.get("coverage_ok"):

            return updated_schedule, {
                "resolved": True,
                "method": "OFF_AGENT_COVER",
                "leave": leave_result,
                "coverage_before": coverage_before,
                "coverage_after": coverage_after,
                "replacement": replacement_result,
                "message": (
                    f"Leave resolved using OFF agent "
                    f"{replacement_result['replacement_agent']}."
                ),
            }

    # -------------------------------------------------
    # Step 4: Try safe shift transfer
    # -------------------------------------------------

    transfer_candidates = find_safe_shift_transfer_candidates(
        schedule=updated_schedule,
        shift_requirements=shift_requirements,
        leave_result=leave_result,
    )

    if transfer_candidates:

        updated_schedule, transfer_result = (
            assign_safe_shift_transfer(
                schedule=updated_schedule,
                leave_result=leave_result,
                transfer_candidates=transfer_candidates,
            )
        )

        coverage_after = calculate_leave_coverage(
            schedule=updated_schedule,
            shift_requirements=shift_requirements,
            leave_result=leave_result,
        )

        if coverage_after.get("coverage_ok"):

            return updated_schedule, {
                "resolved": True,
                "method": "SHIFT_TRANSFER",
                "leave": leave_result,
                "coverage_before": coverage_before,
                "coverage_after": coverage_after,
                "transfer": transfer_result,
                "message": (
                    f"Leave resolved by moving "
                    f"{transfer_result['replacement_agent']} "
                    f"from "
                    f"{transfer_result['source_shift_code']} "
                    f"to "
                    f"{transfer_result['target_shift_code']}."
                ),
            }

    # -------------------------------------------------
    # Step 5: No safe option found
    # -------------------------------------------------

    final_coverage = calculate_leave_coverage(
        schedule=updated_schedule,
        shift_requirements=shift_requirements,
        leave_result=leave_result,
    )

    return updated_schedule, {
        "resolved": False,
        "method": "UNRESOLVED",
        "leave": leave_result,
        "coverage_before": coverage_before,
        "coverage_after": final_coverage,
        "message": (
            "Leave could not be covered safely. "
            "No eligible OFF agent or safe shift-transfer "
            "candidate was available."
        ),
    }

