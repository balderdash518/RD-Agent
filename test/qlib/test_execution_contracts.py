"""Exercise the actual runner/proposal/feedback seams without LLM or Docker calls."""

import json
import re
from types import SimpleNamespace
from unittest.mock import Mock

import pandas as pd
import pytest

from rdagent.components.coder.factor_coder.factor import FactorExperiment, FactorFBWorkspace, FactorTask
from rdagent.components.coder.model_coder.model import ModelTask, ModelFBWorkspace
from rdagent.core.exception import FactorEmptyError
from rdagent.core.proposal import Hypothesis, HypothesisFeedback, Trace
from rdagent.scenarios.qlib.developer.factor_runner import QlibFactorRunner
from rdagent.scenarios.qlib.developer.model_runner import QlibModelRunner
from rdagent.scenarios.qlib.experiment.factor_experiment import QlibFactorExperiment
from rdagent.scenarios.qlib.experiment.model_experiment import QlibModelExperiment
from rdagent.scenarios.qlib.proposal.factor_proposal import QlibFactorHypothesis2Experiment, QlibFactorHypothesisGen
from rdagent.scenarios.qlib.proposal.model_proposal import QlibModelHypothesisGen, QlibModelHypothesis2Experiment
from rdagent.scenarios.qlib.proposal import quant_proposal
from rdagent.scenarios.qlib.developer import feedback

pytestmark = pytest.mark.offline
ALL_METRICS = feedback.IMPORTANT_METRICS + [
    "1day.excess_return_without_cost.annualized_return",
    "1day.excess_return_without_cost.max_drawdown",
]


@pytest.fixture(autouse=True)
def no_render_logging(monkeypatch):
    monkeypatch.setattr("rdagent.utils.agent.tpl.logger.log_object", lambda *args, **kwargs: None)
    monkeypatch.setattr("rdagent.scenarios.qlib.developer.factor_runner.qlib_data_fingerprint", lambda *_: "fixture-data")
    monkeypatch.setattr("rdagent.scenarios.qlib.developer.model_runner.qlib_data_fingerprint", lambda *_: "fixture-data")


def hypothesis(name="test", action=None):
    hyp = Hypothesis(name, "test", "", "", "", "")
    if action:
        hyp.action = action
    return hyp


def factor_exp(name="signal", action="factor"):
    task = FactorTask(name, "same description", "x")
    exp = QlibFactorExperiment([task], hypothesis=hypothesis(name, action))
    ws = FactorFBWorkspace(target_task=task)
    ws.file_dict = {"factor.py": "signal = close.shift(1)"}
    exp.sub_workspace_list = [ws]
    exp.result = pd.Series({metric: 0.02 for metric in ALL_METRICS})
    exp.stdout = "BEST_TRAINING_LOG_" + name
    return exp


def model_exp(name="net"):
    task = ModelTask(
        name, "description", "linear", hyperparameters={}, training_hyperparameters={}, model_type="Tabular"
    )
    exp = QlibModelExperiment([task], hypothesis=hypothesis(name, "model"))
    ws = ModelFBWorkspace(target_task=task)
    ws.file_dict = {"model.py": "class Net: pass"}
    exp.sub_workspace_list = [ws]
    exp.result = pd.Series({metric: 0.03 for metric in ALL_METRICS})
    exp.stdout = "MODEL_TRAINING_LOG"
    return exp


def trace(*trials):
    value = Trace(Mock())
    value.hist = list(trials)
    return value


def accepted(exp):
    return exp, HypothesisFeedback("accepted", decision=True)


@pytest.mark.parametrize("runner_cls", [QlibFactorRunner, QlibModelRunner])
def test_cached_results_restore_candidate_and_all_dependency_logs_without_aliasing(runner_cls):
    current = QlibFactorExperiment([])
    current.based_experiments = [QlibFactorExperiment([]), QlibFactorExperiment([])]
    cached = factor_exp()
    cached.based_experiments = [factor_exp("base1"), factor_exp("base2")]
    current.based_experiments[0].result = pd.Series({"IC": 0.01})
    runner_cls(None).assign_cached_result(current, cached)
    assert current.stdout == cached.stdout
    assert [base.stdout for base in current.based_experiments] == [base.stdout for base in cached.based_experiments]
    current.result.loc["IC"] = 999
    assert cached.result.loc["IC"] != 999


@pytest.mark.parametrize(
    "runner_cls,make_exp,code_name",
    [
        (QlibFactorRunner, factor_exp, "factor.py"),
        (QlibModelRunner, model_exp, "model.py"),
    ],
)
def test_cache_tracks_implementation_dependencies_and_base_python(runner_cls, make_exp, code_name):
    exp = make_exp()
    dependency = factor_exp("dependency")
    exp.based_experiments = [dependency]
    runner = runner_cls(None)
    first = runner.get_cache_key(exp)
    exp.sub_workspace_list[0].file_dict[code_name] += "\n# changed"
    second = runner.get_cache_key(exp)
    assert second != first
    dependency.sub_workspace_list[0].file_dict["factor.py"] += "\n# changed dependency"
    third = runner.get_cache_key(exp)
    assert third != second
    exp.base_feature_codes = {"custom.py": "x = close"}
    assert runner.get_cache_key(exp) != third
    # Workspace UUIDs must not invalidate an otherwise identical implementation.
    before = runner.get_cache_key(exp)
    exp.sub_workspace_list[0].workspace_path /= "different_path"
    assert runner.get_cache_key(exp) == before


@pytest.mark.parametrize("only_duplicates", [False, True])
def test_duplicate_filter_controls_actual_coder_tasks(only_duplicates):
    old = factor_exp("old")
    response = {"old": {"description": "duplicate", "formulation": "x", "variables": {}}}
    if not only_duplicates:
        response["new"] = {"description": "new", "formulation": "y", "variables": {}}
    converter = QlibFactorHypothesis2Experiment()
    if only_duplicates:
        with pytest.raises(FactorEmptyError, match="duplicate"):
            converter.convert_response(json.dumps(response), hypothesis(), trace(accepted(old)))
    else:
        exp = converter.convert_response(json.dumps(response), hypothesis(), trace(accepted(old)))
        assert [task.factor_name for task in exp.sub_tasks] == ["new"]
        assert len(exp.sub_workspace_list) == 1


@pytest.mark.parametrize("action,expected_ids", [("factor", [1, 3, 4]), ("model", [2, 3, 4])])
def test_quant_context_has_global_chronological_ids_and_complete_sota(monkeypatch, action, expected_ids):
    monkeypatch.setattr(quant_proposal.QUANT_PROP_SETTING, "action_selection", "random")
    monkeypatch.setattr(quant_proposal.random, "choice", lambda choices: action)
    value = trace(
        accepted(factor_exp("f1")), accepted(model_exp()), accepted(factor_exp("f2")), accepted(model_exp("net2"))
    )
    context, _ = quant_proposal.QlibQuantHypothesisGen(Mock()).prepare_context(value)
    ids = [int(i) for i in re.findall(r"# Trial (\d+):", context["hypothesis_and_feedback"])]
    assert ids == expected_ids
    sota = context["sota_hypothesis_and_feedback"]
    if action == "factor":
        assert "BEST_TRAINING_LOG_f1" in sota and "BEST_TRAINING_LOG_f2" in sota
    else:
        assert "MODEL_TRAINING_LOG" in sota and "net2" in sota


def test_model_proposal_and_experiment_share_sota_key_and_trial_ids():
    value = trace(accepted(model_exp("m1")), accepted(factor_exp()), accepted(model_exp("m2")))
    context, _ = QlibModelHypothesisGen(Mock()).prepare_context(value)
    assert "MODEL_TRAINING_LOG" in context["sota_hypothesis_and_feedback"]
    context, _ = QlibModelHypothesis2Experiment().prepare_context(hypothesis(), value)
    assert re.findall(r"# Trial (\d+):", context["hypothesis_and_feedback"]) == ["1", "3"]
    assert "MODEL_TRAINING_LOG" in context["sota_hypothesis_and_feedback"]


def test_scheduler_action_cannot_be_overridden_by_llm(monkeypatch):
    monkeypatch.setattr(quant_proposal.QUANT_PROP_SETTING, "action_selection", "random")
    monkeypatch.setattr(quant_proposal.random, "choice", lambda choices: "factor")
    generator = quant_proposal.QlibQuantHypothesisGen(Mock())
    generator.prepare_context(trace())
    result = generator.convert_response(json.dumps({"action": "model", "hypothesis": "h", "reason": "r"}))
    assert result.action == "factor"


def test_bandit_history_is_recorded_once_per_completed_trial(monkeypatch):
    monkeypatch.setattr(quant_proposal.QUANT_PROP_SETTING, "action_selection", "bandit")
    value = quant_proposal.QuantTrace(Mock())
    value.hist = [accepted(factor_exp())]
    value.controller = Mock()
    value.controller.decide.return_value = "model"
    generator = quant_proposal.QlibQuantHypothesisGen(Mock())
    generator.prepare_context(value)
    generator.prepare_context(value)
    assert value.controller.record.call_count == 1


def test_model_feedback_uses_model_prompt_once_and_model_sota(monkeypatch):
    calls = []

    def backend_call(**kwargs):
        calls.append(kwargs)
        return json.dumps({"Decision": True, "Reasoning": "single-response"})

    monkeypatch.setattr(
        feedback, "APIBackend", lambda: SimpleNamespace(build_messages_and_create_chat_completion=backend_call)
    )
    scen = Mock()
    scen.get_scenario_all_desc.return_value = "TEST SCENARIO"
    # Latest accepted experiment is a factor, but the model comparison must use the last accepted model.
    value = trace(accepted(model_exp("previous_model")), accepted(factor_exp("latest_factor")))
    result = feedback.QlibModelExperiment2Feedback(scen).generate_feedback(model_exp("candidate_model"), value)
    assert len(calls) == 1
    assert "previous_model" in calls[0]["user_prompt"]
    assert "latest_factor" not in calls[0]["user_prompt"]
    assert "model" in calls[0]["system_prompt"].lower()
    assert result.reason == "single-response"


def test_factor_acceptance_defaults_to_repository_llm_decision_not_local_ic_gate(monkeypatch):
    monkeypatch.delenv("QLIB_FACTOR_ACCEPTANCE_POLICY", raising=False)
    candidate = factor_exp("candidate")
    baseline = factor_exp("baseline")
    baseline.result.loc["IC"] = 0.1
    candidate.based_experiments = [baseline]
    monkeypatch.setattr(
        feedback,
        "APIBackend",
        lambda: SimpleNamespace(
            build_messages_and_create_chat_completion=lambda **kwargs: json.dumps({"Replace Best Result": "yes"})
        ),
    )
    scen = Mock()
    scen.get_scenario_all_desc.return_value = "TEST SCENARIO"
    result = feedback.QlibFactorExperiment2Feedback(scen).generate_feedback(candidate, trace())
    assert result.decision is True


def test_default_factor_search_no_longer_imposes_local_formula_only_ban():
    value = trace(*[accepted(factor_exp(f"f{i}")) for i in range(16)])
    context, _ = QlibFactorHypothesisGen(Mock()).prepare_context(value)
    assert "do not train models" not in context["RAG"].lower()


def test_joint_factor_experiment_reuses_accepted_model():
    previous_model = model_exp()
    value = trace(accepted(previous_model), accepted(factor_exp("previous_factor")))
    response = {"new": {"description": "new", "formulation": "y", "variables": {}}}
    exp = QlibFactorHypothesis2Experiment().convert_response(json.dumps(response), hypothesis(action="factor"), value)
    assert exp.based_experiments[-1] is previous_model


def test_joint_model_experiment_receives_all_accepted_factors():
    old_factor = factor_exp("previous_factor")
    previous_model = model_exp()
    value = trace(accepted(old_factor), accepted(previous_model))
    response = {
        "new_model": {
            "description": "new",
            "formulation": "y",
            "variables": {},
            "architecture": "linear",
            "hyperparameters": {},
            "training_hyperparameters": {},
            "model_type": "Tabular",
        }
    }
    exp = QlibModelHypothesis2Experiment().convert_response(json.dumps(response), hypothesis(action="model"), value)
    assert old_factor in exp.based_experiments
    # Model runner uses the empty base-factor sentinel + accepted factors to detect additional features.
    assert sum(isinstance(base, QlibFactorExperiment) for base in exp.based_experiments) == 2
    assert exp.based_experiments[-1] is previous_model


def test_current_protocol_and_custom_features_reach_actual_hypothesis_request(monkeypatch):
    from rdagent.components import proposal
    from rdagent.scenarios.qlib.experiment.factor_experiment import QlibFactorScenario

    scen = QlibFactorScenario.__new__(QlibFactorScenario)
    for name in ("background", "source_data", "interface", "output_format", "simulator"):
        setattr(scen, "_" + name, "description")
    monkeypatch.setenv("QLIB_FACTOR_MARKET", "csi500")
    monkeypatch.setenv("QLIB_FACTOR_TRAIN_START", "2010-01-01")
    monkeypatch.setenv("QLIB_FACTOR_TEST_END", "2026-10-14")
    calls = []

    def completion(*args, **kwargs):
        calls.append((args, kwargs))
        return json.dumps({"hypothesis": "h", "reason": "r"})

    monkeypatch.setattr(
        proposal, "APIBackend", lambda: SimpleNamespace(build_messages_and_create_chat_completion=completion)
    )
    QlibFactorHypothesisGen(scen).gen(trace(), plan={"features": {"CUSTOM_FEATURE": "$close/$open"}})
    system = calls[0][0][1]
    for expected in (
        "csi500",
        "2010-01-01",
        "2026-10-14",
        "CUSTOM_FEATURE",
        "$close/$open",
        "Ref($close, -2)",
        "0.0015",
    ):
        assert expected in system


def test_joint_feedback_compares_current_sota_system_not_stale_model_result(monkeypatch):
    previous_model = model_exp("previous_model")
    previous_factor = factor_exp("current_sota_factor")
    previous_factor.result.loc["1day.excess_return_with_cost.annualized_return"] = 0.123456
    value = trace(accepted(previous_model), accepted(previous_factor))
    response = {"new": {"description": "new", "formulation": "y", "variables": {}}}
    candidate = QlibFactorHypothesis2Experiment().convert_response(
        json.dumps(response), hypothesis(action="factor"), value
    )
    candidate.result = previous_factor.result.copy()
    calls = []

    def completion(**kwargs):
        calls.append(kwargs)
        return json.dumps({"Replace Best Result": "no"})

    monkeypatch.setattr(
        feedback, "APIBackend", lambda: SimpleNamespace(build_messages_and_create_chat_completion=completion)
    )
    scen = Mock()
    scen.get_scenario_all_desc.return_value = "TEST SCENARIO"
    feedback.QlibFactorExperiment2Feedback(scen).generate_feedback(candidate, value)
    assert "SOTA Result is 0.123456" in calls[0]["user_prompt"]
    assert "Current Training Log" in calls[0]["user_prompt"]


def test_data_fingerprint_changes_when_prices_change_but_calendar_does_not(tmp_path):
    from rdagent.scenarios.qlib.developer.utils import qlib_data_fingerprint

    (tmp_path / "calendars").mkdir()
    (tmp_path / "calendars/day.txt").write_text("2026-10-14")
    (tmp_path / "features/sh600000").mkdir(parents=True)
    feature = tmp_path / "features/sh600000/close.day.bin"
    feature.write_bytes(b"1234")
    first = qlib_data_fingerprint(str(tmp_path))
    feature.write_bytes(b"changed prices")
    assert qlib_data_fingerprint(str(tmp_path)) != first


@pytest.mark.parametrize("action", ["factor", "model"])
def test_joint_runner_replay_uses_other_branch_outputs(monkeypatch, tmp_path, action):
    from rdagent.scenarios.qlib.developer import factor_runner, model_runner

    previous_model = model_exp()
    old_factor = factor_exp("old_factor")
    value = trace(accepted(previous_model), accepted(old_factor))
    if action == "factor":
        response = {"new": {"description": "new", "formulation": "y", "variables": {}}}
        candidate = QlibFactorHypothesis2Experiment().convert_response(
            json.dumps(response), hypothesis(action=action), value
        )
        runner = QlibFactorRunner(None)
    else:
        candidate = model_exp("candidate")
        candidate.based_experiments = [QlibFactorExperiment([]), old_factor, previous_model]
        runner = QlibModelRunner(None)
    candidate.base_features = {"base": "$close/$open"}
    candidate.experiment_workspace.workspace_path = tmp_path
    index = pd.MultiIndex.from_product([pd.to_datetime(["2026-10-14"]), ["SH600000"]], names=["datetime", "instrument"])

    def process(exp):
        return pd.DataFrame({"old" if isinstance(exp, list) else "new": [1.0]}, index=index)

    monkeypatch.setattr(factor_runner, "process_factor_data", process)
    monkeypatch.setattr(model_runner, "process_factor_data", process)
    monkeypatch.setattr(runner, "deduplicate_new_factors", lambda old, new: new, raising=False)
    calls = []

    def execute(qlib_config_name, run_env):
        calls.append((qlib_config_name, run_env))
        return previous_model.result.copy(), "REPLAY_TRAINING_LOG"

    monkeypatch.setattr(candidate.experiment_workspace, "execute", execute)
    runner.develop.__wrapped__(runner, candidate)
    expected_config = "conf_combined_factors_sota_model.yaml" if action == "factor" else "conf_sota_factors_model.yaml"
    assert calls[0][0] == expected_config
    assert int(calls[0][1]["num_features"]) == (3 if action == "factor" else 2)
    assert candidate.stdout == "REPLAY_TRAINING_LOG"


def test_model_baseline_feature_dimension_comes_from_current_plan(monkeypatch, tmp_path):
    exp = model_exp()
    exp.base_features = {"single_feature": "$close/$open"}
    exp.experiment_workspace.workspace_path = tmp_path
    calls = []

    def execute(qlib_config_name, run_env):
        calls.append(run_env)
        return exp.result, "TRAINING_LOG"

    monkeypatch.setattr(exp.experiment_workspace, "execute", execute)
    runner = QlibModelRunner(None)
    runner.develop.__wrapped__(runner, exp)
    assert calls[0]["num_features"] == "1"
    assert calls[0]["early_stop"] == "10"


def test_real_runner_cache_miss_then_hit_preserves_training_log(monkeypatch, tmp_path):
    from rdagent.core.conf import RD_AGENT_SETTINGS
    from rdagent.scenarios.qlib.experiment.workspace import QlibFBWorkspace

    monkeypatch.setattr(RD_AGENT_SETTINGS, "cache_with_pickle", True)
    monkeypatch.setattr(RD_AGENT_SETTINGS, "pickle_cache_folder_path_str", str(tmp_path / "cache"))
    calls = []

    def execute(self, **kwargs):
        calls.append(kwargs)
        return pd.Series({"IC": 0.02}), "[42] train's l2: 0.99 valid's l2: 0.998"

    monkeypatch.setattr(QlibFBWorkspace, "execute", execute)
    runner = QlibFactorRunner(None)
    first = runner.develop(QlibFactorExperiment([]))
    second = runner.develop(QlibFactorExperiment([]))
    assert len(calls) == 1
    assert first._rdagent_cache_hit is False
    assert second._rdagent_cache_hit is True
    assert first.stdout == second.stdout and "[42]" in second.stdout


def test_explicit_ic_policy_remains_available(monkeypatch):
    monkeypatch.setenv("QLIB_FACTOR_ACCEPTANCE_POLICY", "ic")
    candidate = factor_exp("candidate")
    baseline = factor_exp("baseline")
    baseline.result.loc["IC"] = 0.1
    candidate.based_experiments = [baseline]
    monkeypatch.setattr(
        feedback,
        "APIBackend",
        lambda: SimpleNamespace(
            build_messages_and_create_chat_completion=lambda **kwargs: json.dumps({"Replace Best Result": "yes"})
        ),
    )
    scen = Mock()
    scen.get_scenario_all_desc.return_value = "TEST SCENARIO"
    result = feedback.QlibFactorExperiment2Feedback(scen).generate_feedback(candidate, trace())
    assert result.decision is False
    assert "explicit IC policy" in result.reason


def test_training_log_keeps_positive_and_scientific_pytorch_scores():
    from rdagent.scenarios.qlib.experiment.workspace import extract_training_log

    raw = "noise\nEpoch3: train 1.2e-3, valid +0.045\nbest score: 0.045 @ 3 epoch\n"
    assert extract_training_log(raw).splitlines() == [
        "Epoch3: train 1.2e-3, valid +0.045",
        "best score: 0.045 @ 3 epoch",
    ]
