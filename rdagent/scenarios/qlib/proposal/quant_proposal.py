import json
import random
from typing import Tuple

from rdagent.app.qlib_rd_loop.conf import QUANT_PROP_SETTING
from rdagent.components.proposal import FactorAndModelHypothesisGen
from rdagent.core.proposal import Hypothesis, Scenario, Trace
from rdagent.oai.llm_utils import APIBackend
from rdagent.scenarios.qlib.proposal.bandit import (
    EnvController,
    extract_metrics_from_experiment,
)
from rdagent.utils.agent.tpl import T
from rdagent.scenarios.qlib.proposal.factor_proposal import _sota_hypothesis_and_feedback


class QuantTrace(Trace):
    def __init__(self, scen: Scenario) -> None:
        super().__init__(scen)
        # Initialize the controller with default weights
        self.controller = EnvController()
        self._bandit_schema = 2
        self._bandit_recorded_trials = 0


class QlibQuantHypothesis(Hypothesis):
    def __init__(
        self,
        hypothesis: str,
        reason: str,
        concise_reason: str,
        concise_observation: str,
        concise_justification: str,
        concise_knowledge: str,
        action: str,
    ) -> None:
        super().__init__(
            hypothesis, reason, concise_reason, concise_observation, concise_justification, concise_knowledge
        )
        self.action = action

    def __str__(self) -> str:
        return f"""Chosen Action: {self.action}
Hypothesis: {self.hypothesis}
Reason: {self.reason}
"""


class QlibQuantHypothesisGen(FactorAndModelHypothesisGen):
    def __init__(self, scen: Scenario) -> Tuple[dict, bool]:
        super().__init__(scen)

    def prepare_context(self, trace: Trace) -> Tuple[dict, bool]:

        # ========= Bandit ==========
        if QUANT_PROP_SETTING.action_selection == "bandit":
            if getattr(trace, "_bandit_schema", None) != 2:
                # Old checkpoints used a different metric orientation and posterior update.
                trace.controller = EnvController()
                trace._bandit_schema = 2
                trace._bandit_recorded_trials = 0
            recorded = trace._bandit_recorded_trials
            for experiment, feedback in trace.hist[recorded:]:
                if experiment.result is not None:
                    trace.controller.record(extract_metrics_from_experiment(experiment), experiment.hypothesis.action)
            trace._bandit_recorded_trials = len(trace.hist)
            if len(trace.hist) > 0:
                metric = extract_metrics_from_experiment(trace.hist[-1][0])
                action = trace.controller.decide(metric)
            else:
                action = "factor"
        # ========= LLM ==========
        elif QUANT_PROP_SETTING.action_selection == "llm":
            hypothesis_and_feedback = (
                T("scenarios.qlib.prompts:hypothesis_and_feedback").r(trace=trace)
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

            system_prompt = T("scenarios.qlib.prompts:action_gen.system").r()
            user_prompt = T("scenarios.qlib.prompts:action_gen.user").r(
                hypothesis_and_feedback=hypothesis_and_feedback,
                last_hypothesis_and_feedback=last_hypothesis_and_feedback,
            )
            resp = APIBackend().build_messages_and_create_chat_completion(user_prompt, system_prompt, json_mode=True)

            action = json.loads(resp).get("action", "factor")
        # ========= random ==========
        elif QUANT_PROP_SETTING.action_selection == "random":
            action = random.choice(["factor", "model"])
        else:
            raise ValueError(f"Unknown action selection strategy: {QUANT_PROP_SETTING.action_selection}")
        if action not in {"factor", "model"}:
            raise ValueError(f"Invalid scheduled action: {action}")
        self.targets = action

        qaunt_rag = None
        if action == "factor":
            if len(trace.hist) < 6:
                qaunt_rag = "Try the easiest and fastest factors to experiment with from various perspectives first."
            else:
                qaunt_rag = "Now, you need to try factors that can achieve high IC (e.g., machine learning-based factors)! Do not include factors that are similar to those in the SOTA factor library!"
        elif action == "model":
            qaunt_rag = "Market data may be time-series, and GRU/LSTM models are suitable. Do not generate GNN models for now. Choose model size using the actual configured dataset and training logs, not a fixed assumed sample count. You can keep a good architecture and adjust its training or model hyperparameters."

        if len(trace.hist) == 0:
            hypothesis_and_feedback = "No previous hypothesis and feedback available since it's the first round."
        else:
            specific_trace = Trace(trace.scen)
            if action == "factor":
                # all factor experiments and the SOTA model experiment
                model_inserted = False
                trial_ids = []
                for i in range(len(trace.hist) - 1, -1, -1):  # Reverse iteration
                    if trace.hist[i][0].hypothesis.action == "factor":
                        specific_trace.hist.insert(0, trace.hist[i])
                        trial_ids.insert(0, i + 1)
                    elif (
                        trace.hist[i][0].hypothesis.action == "model"
                        and trace.hist[i][1].decision is True
                        and model_inserted == False
                    ):
                        specific_trace.hist.insert(0, trace.hist[i])
                        trial_ids.insert(0, i + 1)
                        model_inserted = True
            elif action == "model":
                # all model experiments and all SOTA factor experiments
                factor_inserted = False
                trial_ids = []
                for i in range(len(trace.hist) - 1, -1, -1):  # Reverse iteration
                    if trace.hist[i][0].hypothesis.action == "model":
                        specific_trace.hist.insert(0, trace.hist[i])
                        trial_ids.insert(0, i + 1)
                    elif (
                        trace.hist[i][0].hypothesis.action == "factor"
                        and trace.hist[i][1].decision is True
                        and factor_inserted == False
                    ):
                        specific_trace.hist.insert(0, trace.hist[i])
                        trial_ids.insert(0, i + 1)
                        factor_inserted = True
            if len(specific_trace.hist) > 0:
                hypothesis_and_feedback = T("scenarios.qlib.prompts:hypothesis_and_feedback").r(
                    trace=specific_trace,
                    trial_ids=trial_ids,
                )
            else:
                hypothesis_and_feedback = "No previous hypothesis and feedback available."

        last_hypothesis_and_feedback = None
        for i in range(len(trace.hist) - 1, -1, -1):
            if trace.hist[i][0].hypothesis.action == action:
                last_hypothesis_and_feedback = T("scenarios.qlib.prompts:last_hypothesis_and_feedback").r(
                    experiment=trace.hist[i][0], feedback=trace.hist[i][1]
                )
                break

        sota_hypothesis_and_feedback = None
        if action == "factor":
            sota_hypothesis_and_feedback = _sota_hypothesis_and_feedback(trace)
        elif action == "model":
            for i in range(len(trace.hist) - 1, -1, -1):
                if trace.hist[i][0].hypothesis.action == "model" and trace.hist[i][1].decision is True:
                    sota_hypothesis_and_feedback = T("scenarios.qlib.prompts:sota_hypothesis_and_feedback").r(
                        experiment=trace.hist[i][0], feedback=trace.hist[i][1]
                    )
                    break

        context_dict = {
            "hypothesis_and_feedback": hypothesis_and_feedback,
            "last_hypothesis_and_feedback": last_hypothesis_and_feedback,
            "sota_hypothesis_and_feedback": sota_hypothesis_and_feedback,
            "RAG": qaunt_rag,
            "hypothesis_output_format": T("scenarios.qlib.prompts:hypothesis_output_format_with_action").r(),
            "hypothesis_specification": (
                T("scenarios.qlib.prompts:factor_hypothesis_specification").r()
                if action == "factor"
                else T("scenarios.qlib.prompts:model_hypothesis_specification").r()
            ),
        }
        return context_dict, True

    def convert_response(self, response: str) -> Hypothesis:
        response_dict = json.loads(response)
        hypothesis = QlibQuantHypothesis(
            hypothesis=response_dict.get("hypothesis"),
            reason=response_dict.get("reason"),
            concise_reason=response_dict.get("concise_reason"),
            concise_observation=response_dict.get("concise_observation"),
            concise_justification=response_dict.get("concise_justification"),
            concise_knowledge=response_dict.get("concise_knowledge"),
            # The scheduler is authoritative; the response may not switch the selected arm.
            action=self.targets,
        )
        return hypothesis
