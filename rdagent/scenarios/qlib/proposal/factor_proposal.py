import json
from typing import List, Tuple

from rdagent.components.coder.factor_coder.factor import FactorExperiment, FactorTask
from rdagent.components.proposal import FactorHypothesis2Experiment, FactorHypothesisGen
from rdagent.core.proposal import Hypothesis, Scenario, Trace
from rdagent.core.exception import FactorEmptyError
from rdagent.scenarios.qlib.experiment.factor_experiment import QlibFactorExperiment
from rdagent.scenarios.qlib.experiment.model_experiment import QlibModelExperiment
from rdagent.scenarios.qlib.experiment.quant_experiment import QlibQuantScenario
from rdagent.utils.agent.tpl import T

QlibFactorHypothesis = Hypothesis


def _is_factor_experiment(experiment) -> bool:
    # Quant traces also hold model experiments; plain factor traces have no `action` on the hypothesis.
    return not hasattr(experiment.hypothesis, "action") or experiment.hypothesis.action == "factor"


def _numbered_factor_hist(trace: Trace) -> list[tuple[int, object, object]]:
    """Factor trials in chronological order, numbered by their position in the full trace (Trial 1 = first loop).

    Both proposal stages use these numbers, so "Trial N" means the same experiment everywhere.
    """
    return [
        (trial_id, experiment, feedback)
        for trial_id, (experiment, feedback) in enumerate(trace.hist, start=1)
        if _is_factor_experiment(experiment)
    ]


def _sota_hypothesis_and_feedback(trace: Trace) -> str:
    # The SOTA library is the union of every accepted factor experiment, so all of them are shown, oldest first.
    accepted = [
        (experiment, feedback)
        for _, experiment, feedback in _numbered_factor_hist(trace)
        if feedback.decision and isinstance(experiment, FactorExperiment)
    ]
    if not accepted:
        return "No factor set has been accepted yet; the SOTA is still the baseline features alone."
    return "\n".join(
        T("scenarios.qlib.prompts:sota_hypothesis_and_feedback").r(experiment=experiment, feedback=feedback)
        for experiment, feedback in accepted
    )


def _former_factors(numbered_hist: list[tuple[int, object, object]]) -> str:
    lines = []
    for trial_id, experiment, feedback in numbered_hist:
        status = "accepted into SOTA" if feedback.decision else "rejected"
        for task in experiment.sub_tasks:
            lines.append(f"- Trial {trial_id} ({status}) {task.factor_name}: {task.factor_formulation}")
    return "\n".join(lines)


class QlibFactorHypothesisGen(FactorHypothesisGen):
    def __init__(self, scen: Scenario) -> Tuple[dict, bool]:
        super().__init__(scen)

    def prepare_context(self, trace: Trace) -> Tuple[dict, bool]:
        hypothesis_and_feedback = (
            T("scenarios.qlib.prompts:hypothesis_and_feedback").r(
                trace=trace,
            )
            if len(trace.hist) > 0
            else "No previous hypothesis and feedback available since it's the first round."
        )
        last_hypothesis_and_feedback = (
            T("scenarios.qlib.prompts:last_hypothesis_and_feedback").r(
                experiment=trace.hist[-1][0], feedback=trace.hist[-1][1]
            )
            if len(trace.hist) > 0
            else "No previous hypothesis and feedback available since it's the first round."
        )

        sota_hypothesis_and_feedback = (
            _sota_hypothesis_and_feedback(trace)
            if len(trace.hist) > 0
            else "No SOTA hypothesis and feedback available since it's the first round."
        )

        context_dict = {
            "hypothesis_and_feedback": hypothesis_and_feedback,
            "last_hypothesis_and_feedback": last_hypothesis_and_feedback,
            "sota_hypothesis_and_feedback": sota_hypothesis_and_feedback,
            "RAG": (
                "Try the easiest and fastest factors to experiment with from various perspectives first."
                if len(trace.hist) < 15
                else "Now, you need to try factors that can achieve high IC (e.g., machine learning-based factors)."
            ),
            "hypothesis_output_format": T("scenarios.qlib.prompts:factor_hypothesis_output_format").r(),
            "hypothesis_specification": T("scenarios.qlib.prompts:factor_hypothesis_specification").r(),
        }
        return context_dict, True

    def convert_response(self, response: str) -> Hypothesis:
        response_dict = json.loads(response)
        hypothesis = QlibFactorHypothesis(
            hypothesis=response_dict.get("hypothesis"),
            reason=response_dict.get("reason"),
            concise_reason=response_dict.get("concise_reason"),
            concise_observation=response_dict.get("concise_observation"),
            concise_justification=response_dict.get("concise_justification"),
            concise_knowledge=response_dict.get("concise_knowledge"),
        )
        return hypothesis


class QlibFactorHypothesis2Experiment(FactorHypothesis2Experiment):
    def prepare_context(self, hypothesis: Hypothesis, trace: Trace) -> Tuple[dict | bool]:
        if isinstance(trace.scen, QlibQuantScenario):
            scenario = trace.scen.get_scenario_all_desc(action="factor")
        else:
            scenario = trace.scen.get_scenario_all_desc()

        experiment_output_format = T("scenarios.qlib.prompts:factor_experiment_output_format").r()

        numbered_hist = _numbered_factor_hist(trace)
        if len(trace.hist) == 0:
            hypothesis_and_feedback = "No previous hypothesis and feedback available since it's the first round."
        elif len(numbered_hist) > 0:
            # Chronological, with the same trial numbers as the hypothesis stage (it used to be rendered newest first,
            # so "Trial 1" meant a different experiment in the two stages).
            specific_trace = Trace(trace.scen)
            specific_trace.hist = [(experiment, feedback) for _, experiment, feedback in numbered_hist]
            hypothesis_and_feedback = T("scenarios.qlib.prompts:hypothesis_and_feedback").r(
                trace=specific_trace,
                trial_ids=[trial_id for trial_id, _, _ in numbered_hist],
            )
        else:
            hypothesis_and_feedback = "No previous hypothesis and feedback available."

        return {
            "target_hypothesis": str(hypothesis),
            "scenario": scenario,
            "hypothesis_and_feedback": hypothesis_and_feedback,
            "experiment_output_format": experiment_output_format,
            "target_list": [],
            "former_targets": _former_factors(numbered_hist),
            "RAG": None,
        }, True

    def convert_response(self, response: str, hypothesis: Hypothesis, trace: Trace) -> FactorExperiment:
        response_dict = json.loads(response)
        tasks = []

        for factor_name in response_dict:
            description = response_dict[factor_name]["description"]
            formulation = response_dict[factor_name]["formulation"]
            variables = response_dict[factor_name]["variables"]
            tasks.append(
                FactorTask(
                    factor_name=factor_name,
                    factor_description=description,
                    factor_formulation=formulation,
                    variables=variables,
                )
            )

        exp = QlibFactorExperiment(tasks, hypothesis=hypothesis)
        exp.based_experiments = [QlibFactorExperiment(sub_tasks=[])] + [
            t[0] for t in trace.hist if t[1] and isinstance(t[0], QlibFactorExperiment)
        ]
        if getattr(hypothesis, "action", None) == "factor":
            latest_model = next(
                (
                    experiment
                    for experiment, feedback in reversed(trace.hist)
                    if feedback.decision and isinstance(experiment, QlibModelExperiment)
                ),
                None,
            )
            if latest_model is not None:
                exp.based_experiments.append(latest_model)

        unique_tasks = []
        for task in tasks:
            duplicate = False
            for based_exp in exp.based_experiments:
                if isinstance(based_exp, QlibModelExperiment):
                    continue
                for sub_task in based_exp.sub_tasks:
                    if task.factor_name == sub_task.factor_name:
                        duplicate = True
                        break
                if duplicate:
                    break
            if not duplicate:
                unique_tasks.append(task)

        if not unique_tasks:
            raise FactorEmptyError("All proposed factor names duplicate accepted factors; generate new factors.")
        # Construct after filtering so tasks and the parallel workspace slots stay aligned.
        filtered_exp = QlibFactorExperiment(unique_tasks, hypothesis=hypothesis)
        filtered_exp.based_experiments = exp.based_experiments
        filtered_exp.comparison_experiment = next(
            (experiment for experiment, feedback in reversed(trace.hist) if feedback.decision),
            exp.based_experiments[-1],
        )
        return filtered_exp
