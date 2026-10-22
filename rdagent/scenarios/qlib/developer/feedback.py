import json
from pathlib import Path
from typing import Dict

import pandas as pd

from rdagent.app.qlib_rd_loop.conf import FactorBasePropSetting
from rdagent.scenarios.qlib.experiment.model_experiment import QlibModelExperiment
from rdagent.core.experiment import Experiment
from rdagent.core.proposal import Experiment2Feedback, HypothesisFeedback, Trace
from rdagent.log import rdagent_logger as logger
from rdagent.oai.llm_utils import APIBackend
from rdagent.scenarios.qlib.experiment.quant_experiment import QlibQuantScenario
from rdagent.utils import convert2bool
from rdagent.utils.agent.tpl import T

DIRNAME = Path(__file__).absolute().resolve().parent

IMPORTANT_METRICS = [
    "IC",
    "Rank IC",
    "1day.excess_return_with_cost.annualized_return",
    "1day.excess_return_with_cost.max_drawdown",
]


def _metric(result, name: str) -> float:
    series = result.iloc[:, 0] if isinstance(result, pd.DataFrame) else result
    return float(series.loc[name])


def ic_accept_decision(current_result, sota_result) -> tuple[bool, str]:
    """Replace SOTA only on a clear IC gain without a Rank IC loss.

    Single-seed annualized return varies by several percentage points between LightGBM seeds, so it cannot
    separate factor sets; IC is far more stable and decides instead.
    """
    from rdagent.app.qlib_rd_loop.conf import FactorBasePropSetting

    fbps = FactorBasePropSetting()
    ic_gain = _metric(current_result, "IC") - _metric(sota_result, "IC")
    rank_ic_gain = _metric(current_result, "Rank IC") - _metric(sota_result, "Rank IC")
    accept = ic_gain >= fbps.accept_min_ic_gain and rank_ic_gain >= fbps.accept_min_rank_ic_gain
    note = (
        f"IC change {ic_gain:+.6f} (need >= {fbps.accept_min_ic_gain:+.6f}), "
        f"Rank IC change {rank_ic_gain:+.6f} (need >= {fbps.accept_min_rank_ic_gain:+.6f})"
    )
    return accept, note


def process_results(current_result, sota_result):
    # CSVs/checkpoints can yield a Series named '0', integer 0, or unnamed.
    # Read metric values directly rather than relying on an incidental column name.
    return "; ".join(
        f"{metric} of Current Result is {_metric(current_result, metric):.6f}, "
        f"of SOTA Result is {_metric(sota_result, metric):.6f}"
        for metric in IMPORTANT_METRICS
    )


class QlibFactorExperiment2Feedback(Experiment2Feedback):
    def generate_feedback(self, exp: Experiment, trace: Trace) -> HypothesisFeedback:
        """
        Generate feedback for the given experiment and hypothesis.

        Args:
            exp (QlibFactorExperiment): The experiment to generate feedback for.
            hypothesis (QlibFactorHypothesis): The hypothesis to generate feedback for.
            trace (Trace): The trace of the experiment.

        Returns:
            Any: The feedback generated for the given experiment and hypothesis.
        """
        hypothesis = exp.hypothesis
        logger.info("Generating feedback...")
        hypothesis_text = hypothesis.hypothesis
        current_result = exp.result
        tasks_factors = [task.get_task_information_and_implementation_result() for task in exp.sub_tasks]
        comparison = getattr(exp, "comparison_experiment", exp.based_experiments[-1])
        sota_result = comparison.result

        # Process the results to filter important metrics
        combined_result = process_results(current_result, sota_result)

        # Generate the system prompt
        if isinstance(self.scen, QlibQuantScenario):
            sys_prompt = T("scenarios.qlib.prompts:factor_feedback_generation.system").r(
                scenario=self.scen.get_scenario_all_desc(action="factor")
            )
        else:
            sys_prompt = T("scenarios.qlib.prompts:factor_feedback_generation.system").r(
                scenario=self.scen.get_scenario_all_desc()
            )

        # Generate the user prompt
        usr_prompt = T("scenarios.qlib.prompts:factor_feedback_generation.user").r(
            hypothesis_text=hypothesis_text,
            task_details=tasks_factors,
            combined_result=combined_result,
            current_training_log=exp.stdout,
            sota_training_log=comparison.stdout,
        )

        # Call the APIBackend to generate the response for hypothesis feedback
        response = APIBackend().build_messages_and_create_chat_completion(
            user_prompt=usr_prompt,
            system_prompt=sys_prompt,
            json_mode=True,
            json_target_type=Dict[str, str | bool | int],
        )

        # Parse the JSON response to extract the feedback
        response_json = json.loads(response)

        # Extract fields from JSON response
        observations = response_json.get("Observations", "No observations provided")
        hypothesis_evaluation = response_json.get("Feedback for Hypothesis", "No feedback provided")
        new_hypothesis = response_json.get("New Hypothesis", "No new hypothesis provided")
        reason = response_json.get("Reasoning", "No reasoning provided")
        llm_decision = convert2bool(response_json.get("Replace Best Result", "no"))

        decision = llm_decision
        if FactorBasePropSetting().acceptance_policy == "ic":
            decision, rule_note = ic_accept_decision(current_result, sota_result)
            logger.info(f"SOTA decision by explicit IC policy: {decision} ({rule_note}); LLM suggested {llm_decision}")
            if decision != llm_decision:
                reason = f"{reason}\n[Decision set by the explicit IC policy: {rule_note}]"

        return HypothesisFeedback(
            observations=observations,
            hypothesis_evaluation=hypothesis_evaluation,
            new_hypothesis=new_hypothesis,
            reason=reason,
            decision=decision,
        )


class QlibModelExperiment2Feedback(Experiment2Feedback):
    def generate_feedback(self, exp: Experiment, trace: Trace) -> HypothesisFeedback:
        """
        Generate feedback for the given experiment and hypothesis.

        Args:
            exp (QlibModelExperiment): The experiment to generate feedback for.
            hypothesis (QlibModelHypothesis): The hypothesis to generate feedback for.
            trace (Trace): The trace of the experiment.

        Returns:
            HypothesisFeedback: The feedback generated for the given experiment and hypothesis.
        """
        hypothesis = exp.hypothesis
        logger.info("Generating feedback...")

        # Generate the system prompt
        if isinstance(self.scen, QlibQuantScenario):
            sys_prompt = T("scenarios.qlib.prompts:model_feedback_generation.system").r(
                scenario=self.scen.get_scenario_all_desc(action="model")
            )
        else:
            sys_prompt = T("scenarios.qlib.prompts:model_feedback_generation.system").r(
                scenario=self.scen.get_scenario_all_desc()
            )

        # Generate the user prompt
        # In a joint trace, the most recent accepted experiment may be a factor set,
        # not a model. Do not present factor.py as the previous model implementation.
        SOTA_experiment = next(
            (
                experiment
                for experiment, fb in reversed(trace.hist)
                if fb.decision and isinstance(experiment, QlibModelExperiment)
            ),
            None,
        )
        SOTA_hypothesis = SOTA_experiment.hypothesis if SOTA_experiment is not None else None
        comparison = getattr(exp, "comparison_experiment", None) or SOTA_experiment
        user_prompt = T("scenarios.qlib.prompts:model_feedback_generation.user").r(
            sota_hypothesis=SOTA_hypothesis,
            sota_task=SOTA_experiment.sub_tasks[0].get_task_information() if SOTA_hypothesis else None,
            sota_code=SOTA_experiment.sub_workspace_list[0].file_dict.get("model.py") if SOTA_hypothesis else None,
            sota_result=comparison.result.loc[IMPORTANT_METRICS] if comparison is not None else None,
            has_sota_result=comparison is not None,
            hypothesis=hypothesis,
            exp=exp,
            exp_result=exp.result.loc[IMPORTANT_METRICS] if exp.result is not None else "execution failed",
        )

        # Call the APIBackend to generate the response for hypothesis feedback
        response = APIBackend().build_messages_and_create_chat_completion(
            user_prompt=user_prompt,
            system_prompt=sys_prompt,
            json_mode=True,
            json_target_type=Dict[str, str | bool | int],
        )

        # Parse the JSON response to extract the feedback
        response_json_hypothesis = json.loads(response)

        return HypothesisFeedback(
            observations=response_json_hypothesis.get("Observations", "No observations provided"),
            hypothesis_evaluation=response_json_hypothesis.get("Feedback for Hypothesis", "No feedback provided"),
            new_hypothesis=response_json_hypothesis.get("New Hypothesis", "No new hypothesis provided"),
            reason=response_json_hypothesis.get("Reasoning", "No reasoning provided"),
            decision=convert2bool(response_json_hypothesis.get("Decision", "false")),
        )
