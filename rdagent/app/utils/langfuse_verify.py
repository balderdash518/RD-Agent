"""Safely verify RD-Agent traces through Langfuse Observations API v2."""

from __future__ import annotations

import argparse
import json
import time
from collections import Counter, defaultdict
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from typing import Any

from dotenv import load_dotenv
from opentelemetry.trace import get_current_span

_EXPECTED_SMOKE_OBSERVATIONS = 2
_WORKFLOW_STAGE_NAMES = {
    "design-experiment",
    "implement-experiment",
    "execute-experiment",
    "evaluate-experiment",
    "record-experiment",
}


def _observation_summary(observation: Any) -> dict[str, Any]:
    status_message = getattr(observation, "status_message", None)
    return {
        "id": getattr(observation, "id", None),
        "trace_id": getattr(observation, "trace_id", None),
        "parent_observation_id": getattr(observation, "parent_observation_id", None),
        "name": getattr(observation, "name", None),
        "type": str(getattr(observation, "type", "")),
        "level": str(getattr(observation, "level", "")),
        "status_message": str(status_message)[:1000] if status_message else None,
    }


def _enum_text(value: Any) -> str:
    return str(getattr(value, "value", value) or "")


def audit_observations(observations: list[Any]) -> dict[str, Any]:
    """Summarize trace integrity, failures, workflow completeness, usage, and cost."""

    ids = {getattr(item, "id", None) for item in observations}
    missing_parent_ids = {
        parent
        for item in observations
        if (parent := getattr(item, "parent_observation_id", None)) is not None and parent not in ids
    }
    names = Counter(str(getattr(item, "name", "")) for item in observations)
    types = Counter(_enum_text(getattr(item, "type", "")) for item in observations)
    errors = [
        _observation_summary(item)
        for item in observations
        if _enum_text(getattr(item, "level", "")).upper().endswith("ERROR")
    ]

    loops: dict[int, set[str]] = defaultdict(set)
    for item in observations:
        name = str(getattr(item, "name", ""))
        metadata = getattr(item, "metadata", None) or {}
        if name not in _WORKFLOW_STAGE_NAMES or not isinstance(metadata, dict):
            continue
        loop_index = metadata.get("loop_index")
        if isinstance(loop_index, int):
            loops[loop_index].add(name)
    incomplete_loops = {
        str(loop_index): sorted(_WORKFLOW_STAGE_NAMES - stages)
        for loop_index, stages in sorted(loops.items())
        if "record-experiment" not in stages
    }

    total_usage = 0
    total_cost = 0.0
    for item in observations:
        usage_details = getattr(item, "usage_details", None) or {}
        if isinstance(usage_details, dict):
            total_usage += int(usage_details.get("total", usage_details.get("total_tokens", 0)) or 0)
        with suppress(TypeError, ValueError):
            total_cost += float(getattr(item, "total_cost", 0) or 0)

    checkpoints = [item for item in observations if getattr(item, "name", None) == "workflow-checkpoint"]
    latest_checkpoint = None
    if checkpoints:
        latest = max(
            checkpoints,
            key=lambda item: getattr(item, "start_time", None) or datetime.min.replace(tzinfo=timezone.utc),
        )
        latest_checkpoint = {
            "start_time": str(getattr(latest, "start_time", "")),
            "input": getattr(latest, "input", None),
            "output": getattr(latest, "output", None),
        }

    stage_counts = {name: names.get(name, 0) for name in sorted(_WORKFLOW_STAGE_NAMES)}
    complete_count = stage_counts["record-experiment"]
    possible_incomplete_workflows = max(stage_counts.values(), default=0) - complete_count

    return {
        "root_observation_present": any(bool(getattr(item, "is_root_observation", False)) for item in observations),
        "missing_parent_ids": sorted(missing_parent_ids),
        "observation_types": dict(sorted(types.items())),
        "workflow_stage_counts": stage_counts,
        "possible_incomplete_workflows": max(possible_incomplete_workflows, 0),
        "incomplete_loops": incomplete_loops,
        "error_count": len(errors),
        "errors": errors,
        "total_usage": total_usage,
        "total_cost": round(total_cost, 8),
        "latest_checkpoint": latest_checkpoint,
    }


def _create_smoke_trace() -> str:
    from rdagent.utils.langfuse_tracing import (  # noqa: PLC0415 - dotenv must load before project settings
        agent_run,
        flush,
        observe,
        update_observation,
    )

    with agent_run(
        "rdagent-langfuse-smoke",
        input_data={"kind": "synthetic", "contains_private_data": False},
        session_id="rdagent-langfuse-verification",
        tags=["rdagent", "smoke-test"],
        metadata={"purpose": "observations-v2-verification"},
    ) as root:
        if root is None:
            message = "Langfuse is disabled or could not initialize"
            raise RuntimeError(message)
        trace_id = f"{get_current_span().get_span_context().trace_id:032x}"
        with observe(
            "rdagent-langfuse-smoke-child",
            as_type="tool",
            input_data={"synthetic": True},
        ) as child:
            update_observation(child, output={"status": "completed"})
        root.score(name="rdagent_langfuse_integration", value=1.0, data_type="BOOLEAN")
        update_observation(root, output={"status": "completed", "trace_id": trace_id})
    flush()
    return trace_id


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trace-id", help="Read all observations belonging to one trace")
    parser.add_argument("--since-minutes", type=int, default=60, help="Lookback used when listing trace roots")
    parser.add_argument("--smoke", action="store_true", help="Create and read back a synthetic nested trace")
    parser.add_argument("--errors-only", action="store_true", help="Only show ERROR observations")
    parser.add_argument("--wait-seconds", type=int, default=30, help="Maximum ingestion wait for --smoke")
    args = parser.parse_args()

    load_dotenv(".env")
    from rdagent.utils.langfuse_tracing import (  # noqa: PLC0415 - dotenv must load before project settings
        get_client,
        get_trace_observations,
        list_trace_roots,
    )

    if get_client() is None:
        message = "Langfuse is disabled, missing credentials, or failed to initialize"
        raise SystemExit(message)

    trace_id = _create_smoke_trace() if args.smoke else args.trace_id
    if trace_id:
        deadline = time.monotonic() + max(args.wait_seconds, 0)
        observations = get_trace_observations(trace_id)
        while args.smoke and not observations and time.monotonic() < deadline:
            time.sleep(1)
            observations = get_trace_observations(trace_id)
        summaries = [_observation_summary(item) for item in observations]
        if args.errors_only:
            summaries = [item for item in summaries if item["level"].endswith("ERROR")]
        payload = {
            "trace_id": trace_id,
            "observation_count": len(observations),
            "audit": audit_observations(observations),
            "observations": summaries,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        if args.smoke and len(observations) < _EXPECTED_SMOKE_OBSERVATIONS:
            message = "Smoke trace was not fully available before the timeout"
            raise SystemExit(message)
        return

    now = datetime.now(timezone.utc)
    roots = list_trace_roots(
        from_start_time=now - timedelta(minutes=max(args.since_minutes, 1)),
        to_start_time=now,
    )
    print(json.dumps([_observation_summary(item) for item in roots], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
