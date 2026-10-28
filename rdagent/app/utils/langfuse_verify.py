"""Safely verify RD-Agent traces through Langfuse Observations API v2."""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from dotenv import load_dotenv
from opentelemetry.trace import get_current_span

_EXPECTED_SMOKE_OBSERVATIONS = 2


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
