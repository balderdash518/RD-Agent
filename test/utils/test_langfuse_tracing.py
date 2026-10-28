from __future__ import annotations

import concurrent.futures
import multiprocessing
import random
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from langfuse import Langfuse
from langfuse.api import Prompt_Text
from langfuse.model import TextPromptClient
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import get_current_span
from rdagent.utils import langfuse_tracing

_LONG_STATUS_LENGTH = 2000
_MAX_EXPORTED_STATUS_LENGTH = 1000


def _current_trace_context(_: object) -> tuple[int, int, bool]:
    context = get_current_span().get_span_context()
    return context.trace_id, context.span_id, context.is_remote


@pytest.mark.offline
def test_redact_secrets_recursively() -> None:
    value = {
        "authorization": "Authorization=Bearer super-secret-token",
        "nested": ["sk-or-v1-abcdefghijklmnopqrstuvwxyz", {"api_key": "sk-abcdefghijklmnopqrstuvwxyz"}],
    }

    redacted = langfuse_tracing.redact_secrets(value)

    assert "super-secret-token" not in str(redacted)
    assert "sk-or-v1-abcdefghijklmnopqrstuvwxyz" not in str(redacted)
    assert "sk-abcdefghijklmnopqrstuvwxyz" not in str(redacted)
    expected_redaction_count = 3
    assert str(redacted).count("[REDACTED]") == expected_redaction_count


@pytest.mark.offline
def test_mask_adapter_accepts_langfuse_keyword_argument() -> None:
    masked = langfuse_tracing.mask_langfuse_data(data={"api_key": "sk-abcdefghijklmnopqrstuvwxyz"})

    assert masked == {"api_key": "[REDACTED]"}


@pytest.mark.offline
def test_export_mask_suppresses_content_and_bounds_error_messages() -> None:
    params = SimpleNamespace(
        spans={
            "span": SimpleNamespace(
                attributes={
                    "langfuse.observation.input": "private factor formula",
                    "langfuse.observation.output": "private response",
                    "langfuse.observation.status_message": "x" * _LONG_STATUS_LENGTH,
                },
            ),
        },
    )

    with patch.dict("os.environ", {"LANGFUSE_CAPTURE_CONTENT": "false"}, clear=False):
        result = langfuse_tracing.mask_otel_spans(params=params)

    attributes = result.span_patches["span"].set_attributes
    assert attributes["langfuse.observation.input"] == "[CONTENT_CAPTURE_DISABLED]"
    assert attributes["langfuse.observation.output"] == "[CONTENT_CAPTURE_DISABLED]"
    assert len(attributes["langfuse.observation.status_message"]) == _MAX_EXPORTED_STATUS_LENGTH


@pytest.mark.offline
def test_trace_ids_do_not_follow_rdagent_random_seed() -> None:
    random.seed(42)
    first = langfuse_tracing.SecureIdGenerator().generate_trace_id()
    random.seed(42)
    second = langfuse_tracing.SecureIdGenerator().generate_trace_id()

    assert first != second


@pytest.mark.offline
def test_workflow_steps_use_semantic_types_and_stable_names() -> None:
    assert langfuse_tracing.workflow_step_type("direct_exp_gen") == "agent"
    assert langfuse_tracing.workflow_step_type("running") == "tool"
    assert langfuse_tracing.workflow_step_type("feedback") == "evaluator"
    assert langfuse_tracing.workflow_step_name("direct_exp_gen") == "design-experiment"
    assert langfuse_tracing.workflow_step_name("running") == "execute-experiment"


@pytest.mark.offline
def test_disabled_tracing_is_a_noop() -> None:
    with patch.dict("os.environ", {"LANGFUSE_ENABLED": "false"}, clear=False):
        langfuse_tracing.reset_tracing_state()
        with langfuse_tracing.observe("ignored", as_type="agent") as observation:
            assert observation is None
        assert langfuse_tracing.get_client() is None


@pytest.mark.offline
def test_content_capture_is_private_by_default() -> None:
    with patch.dict("os.environ", {}, clear=True):
        assert langfuse_tracing.captures_content() is False


@pytest.mark.offline
def test_running_summary_contains_metrics_hashes_cache_and_artifacts(tmp_path: Path) -> None:
    (tmp_path / "qlib_res.csv").write_text("metric,value\nIC,0.02\n", encoding="utf-8")
    task = SimpleNamespace(
        factor_name="private-factor",
        factor_formulation="close / Ref(close, 5) - 1",
        factor_implementation=True,
    )
    result = SimpleNamespace(
        sub_tasks=[task],
        sub_workspace_list=[SimpleNamespace(file_dict={"factor.py": "def factor(): pass"})],
        experiment_workspace=SimpleNamespace(workspace_path=tmp_path),
        result={"IC": 0.02, "Rank IC": 0.03},
        _rdagent_cache_hit=True,
        _rdagent_cache_key="cache-key",
    )

    with patch.dict("os.environ", {"LANGFUSE_CAPTURE_CONTENT": "false"}, clear=False):
        summary = langfuse_tracing.summarize_result("running", result)

    assert summary["metrics"] == {"IC": 0.02, "Rank IC": 0.03}
    assert summary["cache_hit"] is True
    assert summary["cache_key"] == "cache-key"
    assert summary["code_hashes"][0]["file"] == "factor.py"
    assert summary["artifacts"][0]["name"] == "qlib_res.csv"
    assert "name" not in summary["tasks"][0]
    assert summary["tasks"][0]["definition_hash"]


@pytest.mark.offline
def test_running_result_creates_queryable_factor_scores() -> None:
    ic = 0.02
    rank_ic = 0.03
    observation = SimpleNamespace(score=Mock())
    result = SimpleNamespace(
        result={
            "IC": ic,
            "Rank IC": rank_ic,
            "1day.excess_return_without_cost.annualized_return": 0.10,
            "1day.excess_return_with_cost.annualized_return": 0.06,
        },
    )

    langfuse_tracing.score_result(observation, "running", result)

    scores = {call.kwargs["name"]: call.kwargs["value"] for call in observation.score.call_args_list}
    assert scores["execution_success"] == 1.0
    assert scores["factor_ic"] == ic
    assert scores["factor_rank_ic"] == rank_ic
    assert scores["portfolio_annualized_cost_drag"] == pytest.approx(0.04)


@pytest.mark.offline
def test_trace_reads_use_observations_v2_with_cursor_pagination() -> None:
    api = Mock()
    api.observations.get_many.side_effect = [
        SimpleNamespace(data=["root"], meta=SimpleNamespace(cursor="next")),
        SimpleNamespace(data=["child"], meta=SimpleNamespace(cursor=None)),
    ]
    client = SimpleNamespace(api=api)
    langfuse_tracing.reset_tracing_state(client=client)

    observations = langfuse_tracing.get_trace_observations("trace-123")

    assert observations == ["root", "child"]
    expected_page_count = 2
    assert api.observations.get_many.call_count == expected_page_count
    assert api.observations.get_many.call_args_list[0].kwargs["trace_id"] == "trace-123"
    assert api.observations.get_many.call_args_list[1].kwargs["cursor"] == "next"
    langfuse_tracing.reset_tracing_state()


@pytest.mark.offline
def test_agent_chain_is_nested_with_an_in_memory_exporter() -> None:
    exporter = InMemorySpanExporter()
    client = Langfuse(
        public_key="pk-lf-test-project",
        secret_key="sk-lf-test-project",  # noqa: S106 - deliberately fake test credential
        base_url="http://127.0.0.1:1",
        span_exporter=exporter,
    )

    with patch.dict("os.environ", {"LANGFUSE_ENABLED": "true"}, clear=False):
        langfuse_tracing.reset_tracing_state(client=client)
        with langfuse_tracing.agent_run(
            "run-rd-agent",
            input_data={"scenario": "fin-factor"},
            session_id="rdagent-test-session",
            tags=["rdagent", "fin-factor", "agent-chain"],
        ) as root:
            carrier = langfuse_tracing.inject_trace_context()
            with langfuse_tracing.observe("design-experiment", as_type="agent") as child:
                with langfuse_tracing.observe(
                    "generate-chat-response",
                    as_type="generation",
                    input_data=[{"role": "user", "content": "test"}],
                    model="test-model",
                ) as generation:
                    langfuse_tracing.update_observation(
                        generation,
                        output={"content": "done"},
                        usage_details={"input": 1, "output": 1, "total": 2},
                    )
                langfuse_tracing.update_observation(child, output={"status": "completed"})
            langfuse_tracing.update_observation(root, output={"status": "completed"})
        langfuse_tracing.flush()

    spans = {span.name: span for span in exporter.get_finished_spans()}
    assert {"run-rd-agent", "design-experiment", "generate-chat-response"}.issubset(spans)
    assert spans["design-experiment"].parent is not None
    assert spans["design-experiment"].parent.span_id == spans["run-rd-agent"].context.span_id
    assert spans["generate-chat-response"].parent is not None
    assert spans["generate-chat-response"].parent.span_id == spans["design-experiment"].context.span_id
    assert carrier.get("traceparent")
    client.shutdown()
    langfuse_tracing.reset_tracing_state()


@pytest.mark.offline
def test_trace_context_crosses_a_spawned_process() -> None:
    exporter = InMemorySpanExporter()
    client = Langfuse(
        public_key="pk-lf-test-process-context",
        secret_key="sk-lf-test-process-context",  # noqa: S106 - deliberately fake test credential
        base_url="http://127.0.0.1:1",
        span_exporter=exporter,
    )

    with patch.dict("os.environ", {"LANGFUSE_ENABLED": "true"}, clear=False):
        langfuse_tracing.reset_tracing_state(client=client)
        with langfuse_tracing.agent_run(
            "run-rd-agent",
            input_data={"scenario": "fin-factor"},
            session_id="rdagent-process-test",
            tags=["rdagent", "fin-factor"],
        ):
            carrier = langfuse_tracing.inject_trace_context()
            root_context = get_current_span().get_span_context()
            with concurrent.futures.ProcessPoolExecutor(
                max_workers=1,
                mp_context=multiprocessing.get_context("spawn"),
            ) as pool:
                child_trace_id, parent_span_id, is_remote = pool.submit(
                    langfuse_tracing.invoke_with_trace_context,
                    _current_trace_context,
                    None,
                    carrier,
                ).result(timeout=20)

    assert child_trace_id == root_context.trace_id
    assert parent_span_id == root_context.span_id
    assert is_remote is True
    client.shutdown()
    langfuse_tracing.reset_tracing_state()


@pytest.mark.offline
def test_prompt_names_follow_langfuse_folder_rules() -> None:
    assert (
        langfuse_tracing.prompt_name_from_uri("scenarios.qlib.prompts:hypothesis_gen.system_prompt")
        == "rdagent/scenarios/qlib/prompts/hypothesis_gen.system_prompt"
    )
    assert langfuse_tracing.prompt_name_from_uri(".prompts:a|b") == "rdagent/prompts/a-b"


@pytest.mark.offline
def test_generation_links_to_synced_template_prompt() -> None:
    exporter = InMemorySpanExporter()
    client = Langfuse(
        # The SDK shares one exporter per public key, so this test needs its own fake project key.
        public_key="pk-lf-test-prompt-link",
        secret_key="sk-lf-test-prompt-link",  # noqa: S106 - deliberately fake test credential
        base_url="http://127.0.0.1:1",
        span_exporter=exporter,
    )
    name = "rdagent/scenarios/qlib/prompts/hypothesis_gen.system_prompt"
    template = "You are a quant researcher in {{ scenario }}."
    prompt_version = 3
    synced = TextPromptClient(
        Prompt_Text(
            name=name,
            version=prompt_version,
            prompt=template,
            config={},
            labels=["latest"],
            tags=["rdagent"],
            type="text",
        ),
    )

    with (
        patch.dict("os.environ", {"LANGFUSE_ENABLED": "true", "LANGFUSE_LINK_PROMPTS": "true"}, clear=False),
        patch.object(client, "get_prompt", side_effect=RuntimeError("not found")),
        patch.object(client, "create_prompt", return_value=synced) as create_prompt,
    ):
        langfuse_tracing.reset_tracing_state(client=client)
        langfuse_tracing.remember_rendered_prompt(
            "scenarios.qlib.prompts:hypothesis_gen.system_prompt",
            template,
            "You are a quant researcher in A-shares.",
        )
        messages = [
            {"role": "system", "content": "You are a quant researcher in A-shares.\n"},
            {"role": "user", "content": "free-form text"},
        ]
        for _ in range(2):
            prompt, templates = langfuse_tracing.prompt_link_for_messages(messages)
            with langfuse_tracing.observe("generate-chat-response", as_type="generation", prompt=prompt):
                pass
        langfuse_tracing.flush()

    assert templates == ["scenarios.qlib.prompts:hypothesis_gen.system_prompt"]
    create_prompt.assert_called_once()
    assert create_prompt.call_args.kwargs["name"] == name
    assert create_prompt.call_args.kwargs["prompt"] == template
    generations = [span for span in exporter.get_finished_spans() if span.name == "generate-chat-response"]
    expected_generation_count = 2
    assert len(generations) == expected_generation_count
    for span in generations:
        attributes = dict(span.attributes or {})
        assert attributes.get("langfuse.observation.prompt.name") == name
        assert attributes.get("langfuse.observation.prompt.version") == prompt_version
    client.shutdown()
    langfuse_tracing.reset_tracing_state()


@pytest.mark.offline
def test_prompt_linking_is_off_when_disabled() -> None:
    with patch.dict("os.environ", {"LANGFUSE_ENABLED": "true", "LANGFUSE_LINK_PROMPTS": "false"}, clear=False):
        langfuse_tracing.reset_tracing_state()
        langfuse_tracing.remember_rendered_prompt("a.b:c", "t", "rendered")
        assert langfuse_tracing.prompt_link_for_messages([{"role": "system", "content": "rendered"}]) == (None, [])


@pytest.mark.offline
def test_code_avoids_langfuse_apis_removed_in_sdk_v5() -> None:
    removed = re.compile(
        r"api\.(trace\.(get|list)|sessions|legacy|ingestion|scores_v3)\b|set_current_trace_io|set_trace_io"
        r"|blocked_instrumentation_scopes|/api/public/(traces|sessions|ingestion)",
    )
    root = Path(langfuse_tracing.__file__).resolve().parents[1]
    offenders = [
        f"{path.relative_to(root.parent)}:{number}"
        for path in root.rglob("*.py")
        for number, line in enumerate(path.read_text(encoding="utf-8", errors="ignore").splitlines(), start=1)
        if removed.search(line)
    ]

    assert offenders == []
