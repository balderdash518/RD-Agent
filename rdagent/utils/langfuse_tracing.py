"""Optional Langfuse tracing helpers for RD-Agent.

The helpers in this module deliberately degrade to no-ops. Observability must
never prevent an experiment from running, and importing RD-Agent must continue
to work when the optional Langfuse configuration is absent.
"""

from __future__ import annotations

import hashlib
import logging
import math
import os
import re
import secrets
import threading
from collections import OrderedDict
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from functools import lru_cache
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, Literal, TypeVar, cast

import litellm
from langfuse import Langfuse, propagate_attributes
from langfuse.types import MaskOtelSpansParams, MaskOtelSpansResult, OtelSpanPatch
from opentelemetry import context as otel_context
from opentelemetry.propagate import extract, inject
from opentelemetry.sdk.trace.id_generator import IdGenerator

ObservationType = Literal[
    "agent",
    "chain",
    "embedding",
    "evaluator",
    "event",
    "generation",
    "guardrail",
    "retriever",
    "span",
    "tool",
]

_FALSE_VALUES = {"", "0", "false", "no", "off"}
_SECRET_PATTERNS = (
    re.compile(r"(?i)(authorization\s*[=:]\s*)(?:basic|bearer)?\s*[^\s,;]+"),
    re.compile(r"\b(?:sk|pk)-(?:lf-|or-v1-)?[A-Za-z0-9_-]{12,}\b"),
    re.compile(r'(?i)(["\']?(?:api[_-]?key|secret[_-]?key)["\']?\s*[:=]\s*["\']?)[^"\'\s,}]+'),
)


_RENDERED_PROMPT_LIMIT = 512
_MAX_STATUS_MESSAGE_LENGTH = 1000
_REPO_ROOT = Path(__file__).resolve().parents[2]
_LOGGER = logging.getLogger(__name__)
_T = TypeVar("_T")

_QLIB_SCORE_NAMES = {
    "IC": "factor_ic",
    "ICIR": "factor_icir",
    "Rank IC": "factor_rank_ic",
    "Rank ICIR": "factor_rank_icir",
    "1day.excess_return_with_cost.annualized_return": "portfolio_net_annualized_return",
    "1day.excess_return_with_cost.information_ratio": "portfolio_net_information_ratio",
    "1day.excess_return_with_cost.max_drawdown": "portfolio_net_max_drawdown",
    "1day.excess_return_without_cost.annualized_return": "portfolio_gross_annualized_return",
    "1day.excess_return_without_cost.information_ratio": "portfolio_gross_information_ratio",
    "1day.excess_return_without_cost.max_drawdown": "portfolio_gross_max_drawdown",
    "l2.train": "model_train_l2",
    "l2.valid": "model_valid_l2",
}
_ARTIFACT_NAMES = (
    "qlib_res.csv",
    "ret.parquet",
    "combined_factors_df.parquet",
    "pred.pkl",
    "sig_analysis",
    "portfolio_analysis",
)


@dataclass
class _TracingState:
    client: Any | None = None
    initialization_attempted: bool = False
    # rendered prompt text -> (template uri, raw template), most recent last
    rendered_prompts: OrderedDict[str, tuple[str, str]] = field(default_factory=OrderedDict)
    # (prompt name, template hash) -> Langfuse prompt client, or None after a failed sync
    synced_prompts: dict[tuple[str, str], Any | None] = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)
    process_id: int = field(default_factory=os.getpid)


_state = _TracingState()


class SecureIdGenerator(IdGenerator):
    """Generate trace IDs independently of RD-Agent's deterministic RNG seed."""

    @staticmethod
    def _nonzero_id(bits: int) -> int:
        value = 0
        while value == 0:
            value = secrets.randbits(bits)
        return value

    def generate_span_id(self) -> int:
        return self._nonzero_id(64)

    def generate_trace_id(self) -> int:
        return self._nonzero_id(128)


def _is_truthy(name: str, *, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() not in _FALSE_VALUES


def is_enabled() -> bool:
    """Return whether the user explicitly enabled Langfuse tracing."""

    return _is_truthy("LANGFUSE_ENABLED")


def captures_content() -> bool:
    """Return whether prompt/response content may be exported."""

    return _is_truthy("LANGFUSE_CAPTURE_CONTENT", default=False)


def links_prompts() -> bool:
    """Return whether RD-Agent templates are synced to Langfuse Prompt Management."""

    return is_enabled() and _is_truthy("LANGFUSE_LINK_PROMPTS", default=True)


def rdagent_version() -> str | None:
    try:
        return version("rdagent")
    except PackageNotFoundError:
        return None


def redact_secrets(value: Any) -> Any:
    """Recursively redact common credentials before exporting manual spans."""

    if isinstance(value, str):
        redacted = value
        for pattern in _SECRET_PATTERNS:
            redacted = pattern.sub(
                lambda match: f"{match.group(1)}[REDACTED]" if match.lastindex else "[REDACTED]",
                redacted,
            )
        return redacted
    if isinstance(value, dict):
        return {str(key): redact_secrets(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact_secrets(item) for item in value]
    return value


def mask_langfuse_data(*, data: Any) -> Any:
    """Adapt secret redaction to the keyword-only mask API used by Langfuse."""

    return redact_secrets(data)


def mask_otel_spans(*, params: MaskOtelSpansParams) -> MaskOtelSpansResult | None:
    """Redact credentials from every exported OTel string attribute.

    Langfuse's export-stage hook also covers spans created by integrations, not
    only the observations created through this module.
    """

    patches: dict[str, OtelSpanPatch] = {}
    for identifier, span in params.spans.items():
        replacements: dict[str, Any] = {}
        for key, value in span.attributes.items():
            if isinstance(value, str):
                masked = redact_secrets(value)
                if not captures_content() and key in {
                    "langfuse.observation.input",
                    "langfuse.observation.output",
                }:
                    masked = "[CONTENT_CAPTURE_DISABLED]"
                if key == "langfuse.observation.status_message":
                    masked = masked[:_MAX_STATUS_MESSAGE_LENGTH]
                if masked != value:
                    replacements[key] = masked
        if replacements:
            patches[identifier] = OtelSpanPatch(set_attributes=replacements)
    return MaskOtelSpansResult(span_patches=patches) if patches else None


def _git_revision() -> str | None:
    """Read the current Git revision without invoking a subprocess."""

    head = _REPO_ROOT / ".git" / "HEAD"
    try:
        value = head.read_text(encoding="utf-8").strip()
        if value.startswith("ref: "):
            value = (_REPO_ROOT / ".git" / value.removeprefix("ref: ")).read_text(encoding="utf-8").strip()
    except OSError:
        return os.getenv("GIT_COMMIT") or None
    else:
        return value or None


@lru_cache(maxsize=1)
def _source_tree_fingerprint() -> dict[str, Any]:
    """Fingerprint executable research code, including uncommitted changes.

    A Git commit alone is ambiguous in a dirty worktree. Hashing the source
    tree makes every trace comparable without uploading code contents.
    """

    digest = hashlib.sha256()
    files = [
        path
        for path in (_REPO_ROOT / "rdagent").rglob("*")
        if path.is_file() and path.suffix in {".py", ".yaml", ".yml", ".json", ".toml"}
    ]
    files.extend(path for path in (_REPO_ROOT / "constraints").glob("*.txt") if path.is_file())
    files.extend(path for path in (_REPO_ROOT / "requirements").glob("*.txt") if path.is_file())
    requirements = _REPO_ROOT / "requirements.txt"
    if requirements.is_file():
        files.append(requirements)
    total_bytes = 0
    for path in sorted(set(files)):
        relative = path.relative_to(_REPO_ROOT).as_posix()
        digest.update(relative.encode("utf-8"))
        try:
            content = path.read_bytes()
        except OSError:
            continue
        total_bytes += len(content)
        digest.update(content)
    return {"sha256": digest.hexdigest(), "file_count": len(files), "bytes": total_bytes}


def _evaluation_context(scenario: str) -> dict[str, Any]:
    """Return the exact evaluation contract used by a Qlib run."""

    if scenario not in {"fin-factor", "fin-model", "fin-quant"}:
        return {}
    from rdagent.app.qlib_rd_loop.conf import (  # noqa: PLC0415 - optional scenario dependency
        FACTOR_PROP_SETTING,
        MODEL_PROP_SETTING,
        QUANT_PROP_SETTING,
    )
    from rdagent.components.coder.factor_coder.config import (  # noqa: PLC0415 - optional scenario dependency
        FACTOR_COSTEER_SETTINGS,
    )
    from rdagent.scenarios.qlib.experiment.utils import (  # noqa: PLC0415 - optional scenario dependency
        get_hdf_file_profile,
    )

    setting = {
        "fin-factor": FACTOR_PROP_SETTING,
        "fin-model": MODEL_PROP_SETTING,
        "fin-quant": QUANT_PROP_SETTING,
    }[scenario]
    context: dict[str, Any] = {
        key: getattr(setting, key, None)
        for key in (
            "market",
            "benchmark",
            "train_start",
            "train_end",
            "valid_start",
            "valid_end",
            "test_start",
            "test_end",
        )
    }
    context.update(
        {
            "label_expression": "Ref($close, -2)/Ref($close, -1) - 1",
            "prediction_horizon_trading_days": 1,
            "label_lookahead_trading_days": 2,
            "source_data_instruments": FACTOR_COSTEER_SETTINGS.source_data_instruments,
            "evaluation_metric_scope": "combined_experiment",
            "per_factor_metrics_available": False,
            "turnover_metric_available": False,
            # The current feedback loop compares qlib_res metrics produced on
            # the configured test segment. Recording this explicitly prevents
            # users from mistaking an adaptively reused test window for a final
            # untouched holdout.
            "selection_dataset_split": "test",
            "final_holdout_is_untouched": False,
            "selection_bias_risk": "high",
        },
    )
    source_path = Path(FACTOR_COSTEER_SETTINGS.data_folder) / "daily_pv.h5"
    if source_path.exists():
        context["source_data"] = get_hdf_file_profile(source_path)
    return context


def _normalize_hosts() -> None:
    """Keep the SDK and LiteLLM OTEL integration on the same Langfuse host."""

    host = os.getenv("LANGFUSE_BASE_URL") or os.getenv("LANGFUSE_OTEL_HOST") or os.getenv("LANGFUSE_HOST")
    if not host:
        return
    os.environ.setdefault("LANGFUSE_BASE_URL", host)
    os.environ.setdefault("LANGFUSE_OTEL_HOST", host)


def get_client() -> Any | None:
    """Return a cached Langfuse client, or ``None`` when tracing is unavailable."""

    if _state.process_id != os.getpid():
        reset_tracing_state()
    if _state.client is not None:
        return _state.client
    if _state.initialization_attempted or not is_enabled():
        return None

    _state.initialization_attempted = True
    if not os.getenv("LANGFUSE_PUBLIC_KEY") or not os.getenv("LANGFUSE_SECRET_KEY"):
        _LOGGER.warning("Langfuse tracing is enabled but project credentials are not configured")
        return None

    _normalize_hosts()
    try:
        _state.client = Langfuse(
            mask=mask_langfuse_data,
            mask_otel_spans=mask_otel_spans,
            id_generator=SecureIdGenerator(),
            environment=os.getenv("LANGFUSE_TRACING_ENVIRONMENT", "development"),
            release=_git_revision(),
        )
    except Exception as error:  # noqa: BLE001 - telemetry must never stop an experiment
        _LOGGER.warning("Langfuse initialization failed: %s", error)
        _state.client = None
    return _state.client


def configure_litellm() -> bool:
    """Configure LiteLLM for RD-Agent's explicit Langfuse observations.

    RD-Agent creates generation and embedding observations itself.  Removing the
    native callback avoids duplicate or incorrectly-parented observations in
    LiteLLM versions whose OTEL integration expects the proxy package.
    """

    client = get_client()
    if client is None:
        return False

    litellm.callbacks = [callback for callback in (litellm.callbacks or []) if callback != "langfuse_otel"]
    litellm.turn_off_message_logging = not captures_content()
    return True


@contextmanager
def observe(
    name: str,
    *,
    as_type: ObservationType = "span",
    input_data: Any | None = None,
    metadata: dict[str, Any] | None = None,
    model: str | None = None,
    model_parameters: dict[str, Any] | None = None,
    prompt: Any | None = None,
) -> Iterator[Any | None]:
    """Create a current observation while preserving a no-op fallback."""

    client = get_client()
    if client is None:
        yield None
        return

    with client.start_as_current_observation(
        name=name,
        as_type=as_type,
        input=redact_secrets(input_data),
        metadata=redact_secrets(metadata),
        model=model,
        model_parameters=model_parameters,
        prompt=prompt,
    ) as observation:
        yield observation


@contextmanager
def agent_run(
    name: str,
    *,
    input_data: Any,
    session_id: str,
    tags: list[str],
    metadata: dict[str, Any] | None = None,
) -> Iterator[Any | None]:
    """Create the root agent observation and propagate run-level attributes."""

    client = get_client()
    if client is None:
        yield None
        return

    safe_metadata = redact_secrets(metadata or {})
    # Propagated metadata is applied to the current root as well as children.
    # Restrict it to compact scalar correlation fields so it cannot stringify
    # and truncate the root's structured evaluation/source metadata.
    propagated_metadata = {
        key: str(safe_metadata[key])[:200]
        for key in ("run_id", "scenario", "git_commit", "data_fingerprint")
        if safe_metadata.get(key) is not None
    }
    with (
        client.start_as_current_observation(
            name=name,
            as_type="agent",
            input=redact_secrets(input_data),
            metadata=safe_metadata,
        ) as observation,
        # Entered inside the root so the root and every child observation carry these attributes.
        propagate_attributes(
            session_id=session_id,
            tags=tags,
            metadata=propagated_metadata,
            version=rdagent_version(),
            trace_name=name,
        ),
    ):
        yield observation


def update_observation(  # noqa: C901 - mirrors the optional fields supported by the SDK
    observation: Any | None,
    *,
    output: Any | None = None,
    metadata: dict[str, Any] | None = None,
    model: str | None = None,
    usage_details: dict[str, int] | None = None,
    cost_details: dict[str, float] | None = None,
    completion_start_time: datetime | None = None,
    level: Literal["DEBUG", "DEFAULT", "WARNING", "ERROR"] | None = None,
    status_message: str | None = None,
) -> None:
    """Safely add useful result data to an existing observation."""

    if observation is None:
        return
    values: dict[str, Any] = {}
    if output is not None:
        values["output"] = redact_secrets(output)
    if metadata is not None:
        values["metadata"] = redact_secrets(metadata)
    if model is not None:
        values["model"] = model
    if usage_details is not None:
        values["usage_details"] = usage_details
    if cost_details is not None:
        values["cost_details"] = cost_details
    if completion_start_time is not None:
        values["completion_start_time"] = completion_start_time
    if level is not None:
        values["level"] = level
    if status_message is not None:
        values["status_message"] = redact_secrets(status_message)[:_MAX_STATUS_MESSAGE_LENGTH]
    if values:
        try:
            observation.update(**values)
        except Exception as error:  # noqa: BLE001 - telemetry must never stop an experiment
            _LOGGER.debug("Could not update Langfuse observation: %s", error)
            return


def flush() -> None:
    """Flush queued spans for short-lived CLI processes."""

    client = get_client()
    if client is not None:
        try:
            client.flush()
        except Exception as error:  # noqa: BLE001 - telemetry must never stop an experiment
            _LOGGER.warning("Langfuse flush failed: %s", error)


def shutdown() -> None:
    """Flush and close the process-local client without affecting the workload."""

    client = _state.client
    if client is not None:
        try:
            client.shutdown()
        except Exception as error:  # noqa: BLE001 - telemetry must never stop an experiment
            _LOGGER.warning("Langfuse shutdown failed: %s", error)
    reset_tracing_state()


def inject_trace_context() -> dict[str, str]:
    """Serialize the active OTel trace and baggage for another process."""

    carrier: dict[str, str] = {}
    if is_enabled():
        inject(carrier)
    return carrier


def invoke_with_trace_context(func: Callable[[Any], _T], argument: Any, carrier: dict[str, str]) -> _T:
    """Run a picklable callable in a fresh worker while preserving its parent span."""

    reset_tracing_state()
    token = otel_context.attach(extract(carrier)) if carrier else None
    try:
        return func(argument)
    finally:
        shutdown()
        if token is not None:
            otel_context.detach(token)


def session_id_from_log_path(log_path: str | Path) -> str:
    """Build a stable session id that survives checkpoint resume operations."""

    return f"rdagent-{Path(log_path).resolve().name}"


def new_run_id() -> str:
    """Return a correlation id shared by traces, logs, and exported artifacts."""

    return secrets.token_hex(16)


def _qlib_data_fingerprint(provider_uri: str = "~/.qlib/qlib_data/cn_data") -> str:
    parts = []
    for name in ("day.txt", "day_future.txt"):
        path = Path(provider_uri).expanduser() / "calendars" / name
        try:
            stat = path.stat()
            parts.append(f"{name}:{stat.st_size}:{stat.st_mtime_ns}")
        except OSError:
            parts.append(f"{name}:missing")
    return hashlib.sha256(";".join(parts).encode("utf-8")).hexdigest()


def run_metadata(loop: Any, scenario: str, run_id: str) -> dict[str, Any]:
    """Collect reproducibility fields that are safe and useful for filtering."""

    metadata: dict[str, Any] = {
        "run_id": run_id,
        "scenario": scenario,
        "loop_class": type(loop).__name__,
        "git_commit": _git_revision(),
        "source_tree": _source_tree_fingerprint(),
        "capture_content": captures_content(),
    }
    if scenario not in {"fin-factor", "fin-model", "fin-quant"}:
        return metadata

    try:
        from rdagent.oai.llm_conf import LLM_SETTINGS  # noqa: PLC0415 - settings are optional trace enrichment

        evaluation = _evaluation_context(scenario)
        metadata["evaluation"] = evaluation
        metadata["data_fingerprint"] = (evaluation.get("source_data") or {}).get("sha256")
        metadata["qlib_calendar_fingerprint"] = _qlib_data_fingerprint()
        metadata["llm_seed"] = getattr(LLM_SETTINGS, "chat_seed", None)
        metadata["llm_cache_seed"] = getattr(LLM_SETTINGS, "init_chat_cache_seed", None)
        metadata["llm_auto_cache_seed"] = getattr(LLM_SETTINGS, "use_auto_chat_cache_seed_gen", None)
    except Exception as error:  # noqa: BLE001 - context enrichment must remain optional
        metadata["context_error"] = type(error).__name__
    return metadata


def workflow_step_metadata(loop: Any, *, step_name: str, loop_index: int, step_index: int) -> dict[str, Any]:
    """Attach enough context to interpret a step even if its root span is interrupted."""

    metadata: dict[str, Any] = {
        "loop_index": loop_index,
        "step_index": step_index,
        "step": step_name,
    }
    if step_name in {"running", "feedback", "record"}:
        try:
            metadata["evaluation"] = _evaluation_context(scenario_name(loop))
        except Exception as error:  # noqa: BLE001 - trace enrichment must never stop a run
            metadata["evaluation_context_error"] = type(error).__name__
    return metadata


def scenario_name(loop: Any) -> str:
    """Return a stable scenario tag for a workflow loop instance."""

    class_name = type(loop).__name__
    known_names = {
        "FactorRDLoop": "fin-factor",
        "FactorReportLoop": "fin-factor-report",
        "ModelRDLoop": "fin-model",
        "QuantRDLoop": "fin-quant",
    }
    if class_name in known_names:
        return known_names[class_name]
    words = re.sub(r"(?<!^)(?=[A-Z])", "-", class_name).lower()
    return words.removesuffix("-loop")


def workflow_step_type(step_name: str) -> ObservationType:
    """Map RD-Agent workflow stages to Langfuse's semantic observation types."""

    return cast(
        "ObservationType",
        {
            "direct_exp_gen": "agent",
            "coding": "agent",
            "running": "tool",
            "feedback": "evaluator",
            "record": "span",
        }.get(step_name, "span"),
    )


def workflow_step_name(step_name: str) -> str:
    """Return stable, action-oriented observation names."""

    return {
        "direct_exp_gen": "design-experiment",
        "coding": "implement-experiment",
        "running": "execute-experiment",
        "feedback": "evaluate-experiment",
        "record": "record-experiment",
    }.get(step_name, step_name.replace("_", "-"))


def _numeric_metrics(result: Any) -> dict[str, float]:
    raw_metrics = getattr(result, "result", None)
    if raw_metrics is None or not hasattr(raw_metrics, "items"):
        return {}
    metrics: dict[str, float] = {}
    for key, value in raw_metrics.items():
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(number):
            metrics[str(key)] = number
    return metrics


def _task_manifest(result: Any) -> list[dict[str, Any]]:
    tasks = getattr(result, "sub_tasks", []) or []
    manifest = []
    for index, task in enumerate(tasks):
        factor_name = getattr(task, "factor_name", getattr(task, "name", type(task).__name__))
        formulation = getattr(task, "factor_formulation", "") or ""
        definition_hash = hashlib.sha256(f"{factor_name}\n{formulation}".encode()).hexdigest()
        item: dict[str, Any] = {
            "task_index": index,
            "task_type": type(task).__name__,
            "implementation": bool(getattr(task, "factor_implementation", False)),
            "definition_hash": definition_hash,
            "factor_id": definition_hash[:16],
        }
        if captures_content():
            item.update({"name": str(factor_name), "formulation": str(formulation)[:2000]})
        manifest.append(item)
    return manifest


def _baseline_summary(result: Any) -> dict[str, int]:
    experiments = getattr(result, "based_experiments", []) or []
    return {
        "baseline_experiment_count": len(experiments),
        "baseline_factor_count": sum(len(getattr(experiment, "sub_tasks", []) or []) for experiment in experiments),
    }


def _code_hashes(result: Any) -> list[dict[str, str]]:
    hashes = []
    for index, workspace in enumerate(getattr(result, "sub_workspace_list", []) or []):
        files = getattr(workspace, "file_dict", {}) or {}
        for name in ("factor.py", "model.py"):
            content = files.get(name)
            if isinstance(content, str):
                hashes.append(
                    {
                        "workspace_index": str(index),
                        "file": name,
                        "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
                    },
                )
    return hashes


def _artifact_manifest(result: Any) -> list[dict[str, Any]]:
    workspace = getattr(result, "experiment_workspace", None)
    root = getattr(workspace, "workspace_path", None)
    if root is None:
        return []
    root_path = Path(root)
    artifacts = []
    for name in _ARTIFACT_NAMES:
        path = root_path / name
        try:
            stat = path.stat()
        except OSError:
            continue
        artifacts.append(
            {
                "name": name,
                "uri": path.resolve().as_uri(),
                "size_bytes": stat.st_size,
                "modified_ns": stat.st_mtime_ns,
            },
        )
    return artifacts


def summarize_result(step_name: str, result: Any) -> dict[str, Any]:
    """Create a compact, review-friendly output without serializing workspaces."""

    summary: dict[str, Any] = {"status": "completed", "result_type": type(result).__name__}
    if result is None:
        return summary

    if step_name == "direct_exp_gen" and isinstance(result, dict):
        hypothesis = result.get("propose")
        experiment = result.get("exp_gen")
        summary["experiment_type"] = type(experiment).__name__
        summary["subtask_count"] = len(getattr(experiment, "sub_tasks", []) or [])
        summary["tasks"] = _task_manifest(experiment)
        if hypothesis is not None:
            hypothesis_text = str(hypothesis)
            summary["hypothesis_hash"] = hashlib.sha256(hypothesis_text.encode("utf-8")).hexdigest()
            if captures_content():
                summary["hypothesis"] = hypothesis_text[:4000]
    elif step_name in {"coding", "running"}:
        summary["subtask_count"] = len(getattr(result, "sub_tasks", []) or [])
        summary["workspace_count"] = len(getattr(result, "sub_workspace_list", []) or [])
        summary["tasks"] = _task_manifest(result)
        summary["code_hashes"] = _code_hashes(result)
        summary["cache_hit"] = getattr(result, "_rdagent_cache_hit", None)
        summary["cache_key"] = getattr(result, "_rdagent_cache_key", None)
        summary.update(_baseline_summary(result))
        if step_name == "running":
            summary["metrics"] = _numeric_metrics(result)
            summary["artifacts"] = _artifact_manifest(result)
            summary["evaluation_scope"] = "combined_experiment"
            summary["per_factor_metrics_available"] = False
            summary["turnover_metric_available"] = False
    elif step_name == "feedback":
        summary["decision"] = getattr(result, "decision", None)
        summary["acceptable"] = getattr(result, "acceptable", None)
        exception = getattr(result, "exception", None)
        summary["failure_category"] = (
            "execution_failure" if exception is not None else "rejected_by_evaluator" if not bool(result) else None
        )
        if captures_content():
            summary["feedback"] = str(result)[:4000]
    return summary


def score_result(observation: Any | None, step_name: str, result: Any) -> None:
    """Attach queryable numeric and decision scores to the relevant step."""

    if observation is None:
        return
    scores: list[tuple[str, float | str, str, dict[str, Any] | None]] = []
    if step_name == "running":
        metrics = _numeric_metrics(result)
        factor_ids = [item["factor_id"] for item in _task_manifest(result)]
        score_context: dict[str, Any] = {
            "metric_scope": "combined_experiment",
            "factor_ids": factor_ids,
            "factor_count": len(factor_ids),
        }
        scores.append(("execution_success", 1.0, "BOOLEAN", score_context))
        for source_name, score_name in _QLIB_SCORE_NAMES.items():
            if source_name in metrics:
                scores.append(
                    (
                        score_name,
                        metrics[source_name],
                        "NUMERIC",
                        {**score_context, "metric": source_name},
                    ),
                )
        gross = metrics.get("1day.excess_return_without_cost.annualized_return")
        net = metrics.get("1day.excess_return_with_cost.annualized_return")
        if gross is not None and net is not None:
            scores.append(("portfolio_annualized_cost_drag", gross - net, "NUMERIC", score_context))
    elif step_name == "feedback":
        scores.append(("experiment_selected", float(bool(result)), "BOOLEAN", None))
        acceptable = getattr(result, "acceptable", None)
        if acceptable is not None:
            scores.append(("experiment_acceptable", float(bool(acceptable)), "BOOLEAN", None))

    for name, value, data_type, metadata in scores:
        _safe_score(observation, name=name, value=value, data_type=data_type, metadata=metadata)


def _safe_score(
    observation: Any,
    *,
    name: str,
    value: float | str,
    data_type: str,
    metadata: dict[str, Any] | None = None,
    comment: str | None = None,
) -> None:
    """Attach one score without allowing telemetry failures into the workflow."""

    try:
        observation.score(
            name=name,
            value=value,
            data_type=data_type,
            metadata=metadata,
            comment=comment,
        )
    except Exception as error:  # noqa: BLE001 - scoring must never stop an experiment
        _LOGGER.debug("Could not attach Langfuse score %s: %s", name, error)


def score_failure(observation: Any | None, error: BaseException) -> None:
    if observation is None:
        return
    _safe_score(
        observation,
        name="execution_success",
        value=0.0,
        data_type="BOOLEAN",
        comment=type(error).__name__,
    )


def get_trace_observations(
    trace_id: str,
    *,
    fields: str = "core,basic,time,io,metadata,model,usage,prompt,metrics,trace_context",
    expand_metadata: str = "evaluation,source_tree",
) -> list[Any]:
    """Read a complete trace through Observations API v2.

    This replaces the deprecated trace-by-id client endpoint.
    """

    client = get_client()
    if client is None:
        return []
    cursor = None
    observations: list[Any] = []
    while True:
        response = client.api.observations.get_many(
            trace_id=trace_id,
            fields=fields,
            expand_metadata=expand_metadata,
            limit=100,
            cursor=cursor,
        )
        observations.extend(response.data)
        cursor = response.meta.cursor
        if not cursor:
            return observations


def list_trace_roots(
    *,
    from_start_time: datetime,
    to_start_time: datetime,
    fields: str = "core,basic,time,io,metadata,usage,metrics,trace_context",
    expand_metadata: str = "evaluation,source_tree",
) -> list[Any]:
    """List trace roots through Observations API v2 and cursor pagination."""

    client = get_client()
    if client is None:
        return []
    cursor = None
    roots: list[Any] = []
    while True:
        response = client.api.observations.get_many(
            from_start_time=from_start_time,
            to_start_time=to_start_time,
            is_root_observation=True,
            fields=fields,
            expand_metadata=expand_metadata,
            limit=100,
            cursor=cursor,
        )
        roots.extend(response.data)
        cursor = response.meta.cursor
        if not cursor:
            return roots


def litellm_metadata(generation_name: str, existing: dict[str, Any] | None = None) -> dict[str, Any]:
    """Attach stable names and tags to LiteLLM generations."""

    metadata = dict(existing or {})
    if is_enabled():
        metadata.setdefault("generation_name", generation_name)
        metadata.setdefault("tags", ["rdagent"])
    return metadata


def remember_rendered_prompt(uri: str, template: str, rendered: str) -> None:
    """Record which RD-Agent template produced a rendered prompt, for later prompt linking."""

    if not links_prompts() or not rendered.strip():
        return
    with _state.lock:
        _state.rendered_prompts[rendered.strip()] = (uri, template)
        _state.rendered_prompts.move_to_end(rendered.strip())
        while len(_state.rendered_prompts) > _RENDERED_PROMPT_LIMIT:
            _state.rendered_prompts.popitem(last=False)


def prompt_name_from_uri(uri: str) -> str:
    """Map a template uri such as ``scenarios.qlib.prompts:a.b`` to a Langfuse prompt folder path."""

    path_part, _, yaml_key = uri.partition(":")
    segments = [segment for segment in path_part.split(".") if segment]
    if yaml_key:
        segments.append(yaml_key)
    name = "/".join(["rdagent", *segments]).replace("|", "-")
    return re.sub(r"/{2,}", "/", name).strip("/")


def _synced_prompt(uri: str, template: str) -> Any | None:
    """Return the Langfuse prompt version holding ``template``, creating it when the text changed."""

    client = get_client()
    if client is None:
        return None
    name = prompt_name_from_uri(uri)
    key = (name, hashlib.sha256(template.encode("utf-8")).hexdigest())
    with _state.lock:
        if key in _state.synced_prompts:
            return _state.synced_prompts[key]

    prompt = None
    try:
        try:
            latest = client.get_prompt(name, label="latest", max_retries=1, fetch_timeout_seconds=5)
            if latest.prompt == template:
                prompt = latest
        except Exception:  # noqa: BLE001 - a missing prompt is the normal first-run case
            prompt = None
        if prompt is None:
            prompt = client.create_prompt(
                name=name,
                prompt=template,
                type="text",
                tags=["rdagent"],
                commit_message=f"Synced from RD-Agent template {uri}",
            )
    except Exception:  # noqa: BLE001 - telemetry must never stop an experiment
        prompt = None
    with _state.lock:
        _state.synced_prompts[key] = prompt
    return prompt


def prompt_link_for_messages(messages: list[dict[str, Any]]) -> tuple[Any | None, list[str]]:
    """Find the RD-Agent templates behind chat messages.

    Returns the Langfuse prompt to link (the first matched message, normally the
    system prompt) and the uris of every matched template.
    """

    if not links_prompts():
        return None, []
    with _state.lock:
        matches = [
            _state.rendered_prompts.get(content.strip())
            for message in messages
            if isinstance(content := message.get("content"), str)
        ]
    templates = [match for match in matches if match is not None]
    if not templates:
        return None, []
    uri, template = templates[0]
    return _synced_prompt(uri, template), [matched_uri for matched_uri, _ in templates]


def reset_tracing_state(*, client: Any | None = None) -> None:
    """Reset cached state, primarily for tests or reloading environment settings."""

    _state.client = client
    _state.initialization_attempted = client is not None
    _state.rendered_prompts = OrderedDict()
    _state.synced_prompts = {}
    # A lock inherited through fork may remain permanently held in the child.
    _state.lock = threading.Lock()
    _state.process_id = os.getpid()
