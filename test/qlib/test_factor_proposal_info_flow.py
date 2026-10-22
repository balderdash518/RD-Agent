"""What the factor loop actually hands to the LLM: training log, SOTA section, former factors, trial numbering.

These render the real prompt templates (no mocking of T) so a template/context mismatch fails here, not mid-run.
"""

import re
from unittest.mock import Mock

import pandas as pd
import pytest

from rdagent.components.coder.factor_coder.factor import FactorExperiment, FactorTask
from rdagent.core.proposal import Hypothesis, HypothesisFeedback, Trace
from rdagent.scenarios.qlib.experiment.workspace import extract_training_log
from rdagent.scenarios.qlib.proposal.factor_proposal import (
    QlibFactorHypothesis2Experiment,
    QlibFactorHypothesisGen,
)
from rdagent.utils.agent.tpl import T

# Excerpt of a real qrun log from the factor loop (LightGBM), progress bars included.
LGBM_QRUN_LOG = (
    "[8:MainThread](2026-10-20 16:45:10,142) INFO - qlib.timer - [log.py:127] - Time cost: 30.607s | Init data Done\n"
    "Training until validation scores don't improve for 50 rounds\n"
    "[20]\ttrain's l2: 0.996807\tvalid's l2: 0.997819\n"
    "[40]\ttrain's l2: 0.995978\tvalid's l2: 0.997769\n"
    "Early stopping, best iteration is:\n"
    "[42]\ttrain's l2: 0.995903\tvalid's l2: 0.997766\n"
    "[8:MainThread](2026-10-20 16:46:59,298) INFO - qlib.fill_placeholder - [__init__.py:798] - <PRED> lookes like\n"
    "backtest loop:  74%|███████▎  | 536/728 [00:05<00:02, 95.79it/s]\r"
)


@pytest.fixture(autouse=True)
def _no_render_logging(monkeypatch):
    # T.r pickles every render into log/; the mock scenario cannot be pickled and tests should not write run logs.
    monkeypatch.setattr("rdagent.utils.agent.tpl.logger.log_object", lambda *args, **kwargs: None)


def _hypothesis(text: str) -> Hypothesis:
    return Hypothesis(
        hypothesis=text,
        reason="r",
        concise_reason="",
        concise_observation="",
        concise_justification="",
        concise_knowledge="",
    )


def _trial(name: str, formulation: str, accepted: bool) -> tuple[FactorExperiment, HypothesisFeedback]:
    exp = FactorExperiment(
        sub_tasks=[FactorTask(name, f"{name} description", formulation)],
        hypothesis=_hypothesis(f"hypothesis about {name}"),
    )
    exp.result = pd.Series(
        {
            "IC": 0.015,
            "1day.excess_return_without_cost.annualized_return": 0.1,
            "1day.excess_return_without_cost.max_drawdown": -0.1,
        }
    )
    exp.stdout = extract_training_log(LGBM_QRUN_LOG)
    feedback = HypothesisFeedback(
        reason=f"reason {name}",
        decision=accepted,
        observations=f"observation {name}",
        hypothesis_evaluation=f"evaluation {name}",
        new_hypothesis=f"next after {name}",
    )
    return exp, feedback


def _trace(*trials) -> Trace:
    trace = Trace(scen=Mock())
    trace.hist = list(trials)
    return trace


def _trial_headers(text: str) -> list[str]:
    return re.findall(r"# Trial (\d+):", text)


# ---- training log ----------------------------------------------------------------------------------------------------


def test_lightgbm_training_log_keeps_iterations_and_best_iteration():
    log = extract_training_log(LGBM_QRUN_LOG)
    assert log.splitlines() == [
        "Training until validation scores don't improve for 50 rounds",
        "[20]\ttrain's l2: 0.996807\tvalid's l2: 0.997819",
        "[40]\ttrain's l2: 0.995978\tvalid's l2: 0.997769",
        "Early stopping, best iteration is:",
        "[42]\ttrain's l2: 0.995903\tvalid's l2: 0.997766",
    ]
    assert "MainThread" not in log and "backtest loop" not in log


def test_lightgbm_training_log_without_early_stop_and_crlf():
    raw = "[100]\ttrain's l2: 0.99\tvalid's l2: 0.998\r\nDid not meet early stopping. Best iteration is:\r\n[100]\ttrain's l2: 0.99\tvalid's l2: 0.998\r\n"
    assert extract_training_log(raw).splitlines() == [
        "[100]\ttrain's l2: 0.99\tvalid's l2: 0.998",
        "Did not meet early stopping. Best iteration is:",
        "[100]\ttrain's l2: 0.99\tvalid's l2: 0.998",
    ]


def test_pytorch_training_log_unchanged():
    raw = "noise\nEpoch0: train -0.123, valid -0.456\nmore noise\nbest score: -0.456 @ 3 epoch\n"
    assert extract_training_log(raw) == "Epoch0: train -0.123, valid -0.456\nbest score: -0.456 @ 3 epoch"


def test_training_log_reaches_the_prompt():
    exp, feedback = _trial("f1", "x_t", accepted=False)
    rendered = T("scenarios.qlib.prompts:last_hypothesis_and_feedback").r(experiment=exp, feedback=feedback)
    assert "Early stopping, best iteration is:" in rendered and "[42]" in rendered


# ---- SOTA section in the hypothesis stage ----------------------------------------------------------------------------


def test_hypothesis_stage_gets_all_accepted_factor_sets_as_sota():
    trace = _trace(
        _trial("rejected_one", "a_t", accepted=False),
        _trial("accepted_one", "b_t", accepted=True),
        _trial("rejected_two", "c_t", accepted=False),
        _trial("accepted_two", "d_t", accepted=True),
    )
    context, _ = QlibFactorHypothesisGen(Mock()).prepare_context(trace)
    sota = context["sota_hypothesis_and_feedback"]
    assert "accepted_one" in sota and "accepted_two" in sota
    assert sota.index("accepted_one") < sota.index("accepted_two")
    assert "rejected_one" not in sota and "rejected_two" not in sota


def test_hypothesis_stage_sota_when_nothing_accepted_or_first_round():
    gen = QlibFactorHypothesisGen(Mock())
    context, _ = gen.prepare_context(_trace(_trial("rejected_one", "a_t", accepted=False)))
    assert "No factor set has been accepted yet" in context["sota_hypothesis_and_feedback"]
    context, _ = gen.prepare_context(_trace())
    assert "first round" in context["sota_hypothesis_and_feedback"]


def test_sota_section_is_rendered_into_the_user_prompt():
    trace = _trace(_trial("accepted_one", "b_t", accepted=True), _trial("rejected_one", "a_t", accepted=False))
    context, _ = QlibFactorHypothesisGen(Mock()).prepare_context(trace)
    user_prompt = T("components.proposal.prompts:hypothesis_gen.user_prompt").r(
        targets="factors",
        hypothesis_and_feedback=context["hypothesis_and_feedback"],
        last_hypothesis_and_feedback=context["last_hypothesis_and_feedback"],
        sota_hypothesis_and_feedback=context["sota_hypothesis_and_feedback"],
        RAG=context["RAG"],
    )
    assert "Here is the SOTA trail's hypothesis and the corresponding feedback" in user_prompt
    sota_part = user_prompt.split("Here is the SOTA trail's hypothesis")[1]
    assert "accepted_one" in sota_part


# ---- former factors and trial numbering in the experiment-design stage -----------------------------------------------


def test_experiment_stage_lists_former_factors_with_outcome():
    trace = _trace(
        _trial("momentum_5d", "close_t/close_{t-5}-1", accepted=False),
        _trial("reversal_1d", "-(close_t/close_{t-1}-1)", accepted=True),
    )
    context, _ = QlibFactorHypothesis2Experiment().prepare_context(_hypothesis("new"), trace)
    assert context["former_targets"].splitlines() == [
        "- Trial 1 (rejected) momentum_5d: close_t/close_{t-5}-1",
        "- Trial 2 (accepted into SOTA) reversal_1d: -(close_t/close_{t-1}-1)",
    ]

    user_prompt = T("components.proposal.prompts:hypothesis2experiment.user_prompt").r(
        targets="factors",
        target_hypothesis=context["target_hypothesis"],
        hypothesis_and_feedback=context["hypothesis_and_feedback"],
        last_hypothesis_and_feedback="",
        sota_hypothesis_and_feedback="",
        target_list=context["target_list"],
        former_targets=context["former_targets"],
        RAG=context["RAG"],
    )
    assert "Former proposed factors" in user_prompt and "- Trial 1 (rejected) momentum_5d" in user_prompt


def test_trial_numbers_match_between_the_two_stages():
    trace = _trace(*[_trial(f"factor_{i}", f"x_{i}", accepted=(i == 1)) for i in range(4)])
    hyp_context, _ = QlibFactorHypothesisGen(Mock()).prepare_context(trace)
    exp_context, _ = QlibFactorHypothesis2Experiment().prepare_context(_hypothesis("new"), trace)

    assert _trial_headers(hyp_context["hypothesis_and_feedback"]) == ["1", "2", "3", "4"]
    assert _trial_headers(exp_context["hypothesis_and_feedback"]) == ["1", "2", "3", "4"]
    for text in (hyp_context["hypothesis_and_feedback"], exp_context["hypothesis_and_feedback"]):
        trial_2 = text.split("# Trial 2:")[1].split("# Trial 3:")[0]
        assert "factor_1" in trial_2


def test_trial_numbers_follow_the_full_trace_when_model_trials_are_filtered_out():
    model_exp = Mock(sub_tasks=[], hypothesis=Mock(action="model"))
    factor_a, fb_a = _trial("factor_a", "a", accepted=False)
    factor_b, fb_b = _trial("factor_b", "b", accepted=False)
    factor_a.hypothesis.action = "factor"
    factor_b.hypothesis.action = "factor"
    trace = _trace((factor_a, fb_a), (model_exp, Mock(decision=False)), (factor_b, fb_b))

    exp_context, _ = QlibFactorHypothesis2Experiment().prepare_context(_hypothesis("new"), trace)
    assert _trial_headers(exp_context["hypothesis_and_feedback"]) == ["1", "3"]
    assert "- Trial 3 (rejected) factor_b: b" in exp_context["former_targets"]
