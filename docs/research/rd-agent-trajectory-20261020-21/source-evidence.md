# 实际 WSL 运行副本的代码与提示词证据

审计时只读摘录。行号为该副本源文件行号；实际运行行为另由签名工件与 overnight 日志交叉确认。

## rdagent/scenarios/qlib/developer/feedback.py

```text
15: DIRNAME = Path(__file__).absolute().resolve().parent
16: 
17: IMPORTANT_METRICS = [
18:     "IC",
19:     "Rank IC",
20:     "1day.excess_return_with_cost.annualized_return",
21:     "1day.excess_return_with_cost.max_drawdown",
22: ]
23: 
24: 
25: def _metric(result, name: str) -> float:
26:     series = result.iloc[:, 0] if isinstance(result, pd.DataFrame) else result
27:     return float(series.loc[name])
28: 
29: 
30: def ic_accept_decision(current_result, sota_result) -> tuple[bool, str]:
31:     """Replace SOTA only on a clear IC gain without a Rank IC loss.
32: 
33:     Single-seed annualized return varies by several percentage points between LightGBM seeds, so it cannot
34:     separate factor sets; IC is far more stable and decides instead.
35:     """
36:     from rdagent.app.qlib_rd_loop.conf import FactorBasePropSetting
37: 
38:     fbps = FactorBasePropSetting()
39:     ic_gain = _metric(current_result, "IC") - _metric(sota_result, "IC")
40:     rank_ic_gain = _metric(current_result, "Rank IC") - _metric(sota_result, "Rank IC")
41:     accept = ic_gain >= fbps.accept_min_ic_gain and rank_ic_gain >= fbps.accept_min_rank_ic_gain
42:     note = (
43:         f"IC change {ic_gain:+.6f} (need >= {fbps.accept_min_ic_gain:+.6f}), "
44:         f"Rank IC change {rank_ic_gain:+.6f} (need >= {fbps.accept_min_rank_ic_gain:+.6f})"
45:     )
46:     return accept, note
47: 
48: 
49: def process_results(current_result, sota_result):
50:     # Convert the results to dataframes
51:     current_df = pd.DataFrame(current_result)
52:     sota_df = pd.DataFrame(sota_result)
...
85:             exp (QlibFactorExperiment): The experiment to generate feedback for.
86:             hypothesis (QlibFactorHypothesis): The hypothesis to generate feedback for.
87:             trace (Trace): The trace of the experiment.
88: 
89:         Returns:
90:             Any: The feedback generated for the given experiment and hypothesis.
91:         """
92:         hypothesis = exp.hypothesis
93:         logger.info("Generating feedback...")
94:         hypothesis_text = hypothesis.hypothesis
95:         current_result = exp.result
96:         tasks_factors = [task.get_task_information_and_implementation_result() for task in exp.sub_tasks]
97:         sota_result = exp.based_experiments[-1].result
98: 
99:         # Process the results to filter important metrics
100:         combined_result = process_results(current_result, sota_result)
101: 
102:         # Generate the system prompt
103:         if isinstance(self.scen, QlibQuantScenario):
104:             sys_prompt = T("scenarios.qlib.prompts:factor_feedback_generation.system").r(
105:                 scenario=self.scen.get_scenario_all_desc(action="factor")
106:             )
107:         else:
108:             sys_prompt = T("scenarios.qlib.prompts:factor_feedback_generation.system").r(
109:                 scenario=self.scen.get_scenario_all_desc()
110:             )
111: 
112:         # Generate the user prompt
113:         usr_prompt = T("scenarios.qlib.prompts:factor_feedback_generation.user").r(
114:             hypothesis_text=hypothesis_text,
115:             task_details=tasks_factors,
116:             combined_result=combined_result,
117:         )
118: 
119:         # Call the APIBackend to generate the response for hypothesis feedback
120:         response = APIBackend().build_messages_and_create_chat_completion(
121:             user_prompt=usr_prompt,
122:             system_prompt=sys_prompt,
123:             json_mode=True,
124:             json_target_type=Dict[str, str | bool | int],
125:         )
126: 
127:         # Parse the JSON response to extract the feedback
128:         response_json = json.loads(response)
129: 
130:         # Extract fields from JSON response
131:         observations = response_json.get("Observations", "No observations provided")
132:         hypothesis_evaluation = response_json.get("Feedback for Hypothesis", "No feedback provided")
133:         new_hypothesis = response_json.get("New Hypothesis", "No new hypothesis provided")
134:         reason = response_json.get("Reasoning", "No reasoning provided")
135:         llm_decision = convert2bool(response_json.get("Replace Best Result", "no"))
136: 
137:         decision, rule_note = ic_accept_decision(current_result, sota_result)
138:         logger.info(f"SOTA decision by IC rule: {decision} ({rule_note}); LLM suggested {llm_decision}")
139:         if decision != llm_decision:
140:             reason = f"{reason}\n[Decision set by the IC rule, not the LLM suggestion: {rule_note}]"
141: 
142:         return HypothesisFeedback(
143:             observations=observations,
144:             hypothesis_evaluation=hypothesis_evaluation,
145:             new_hypothesis=new_hypothesis,
146:             reason=reason,
147:             decision=decision,
148:         )
149: 
150: 
151: class QlibModelExperiment2Feedback(Experiment2Feedback):
152:     def generate_feedback(self, exp: Experiment, trace: Trace) -> HypothesisFeedback:
153:         """
154:         Generate feedback for the given experiment and hypothesis.
155: 
...
```

## rdagent/scenarios/qlib/proposal/factor_proposal.py

```text
15: class QlibFactorHypothesisGen(FactorHypothesisGen):
16:     def __init__(self, scen: Scenario) -> Tuple[dict, bool]:
17:         super().__init__(scen)
18: 
19:     def prepare_context(self, trace: Trace) -> Tuple[dict, bool]:
20:         hypothesis_and_feedback = (
21:             T("scenarios.qlib.prompts:hypothesis_and_feedback").r(
22:                 trace=trace,
23:             )
24:             if len(trace.hist) > 0
25:             else "No previous hypothesis and feedback available since it's the first round."
26:         )
27:         last_hypothesis_and_feedback = (
28:             T("scenarios.qlib.prompts:last_hypothesis_and_feedback").r(
29:                 experiment=trace.hist[-1][0], feedback=trace.hist[-1][1]
30:             )
31:             if len(trace.hist) > 0
32:             else "No previous hypothesis and feedback available since it's the first round."
33:         )
34: 
35:         context_dict = {
36:             "hypothesis_and_feedback": hypothesis_and_feedback,
37:             "last_hypothesis_and_feedback": last_hypothesis_and_feedback,
38:             # Upstream switched to "machine learning-based factors" after 15 loops; that sent every later loop into
39:             # training models inside factor.py, which timed out or duplicated the downstream LightGBM. Keep the
40:             # guidance on deterministic formula factors for the whole run.
41:             "RAG": (
42:                 "Try the easiest and fastest factors to experiment with from various perspectives first."
43:                 if len(trace.hist) < 15
44:                 else "Explore factor families and raw-data combinations not yet covered by the SOTA library "
45:                 "(e.g. price-volume interaction, intraday range structure, liquidity, conditional or regime-dependent "
46:                 "definitions). Keep every factor a deterministic formula; do not train models inside factor code."
47:             ),
48:             "hypothesis_output_format": T("scenarios.qlib.prompts:factor_hypothesis_output_format").r(),
49:             "hypothesis_specification": T("scenarios.qlib.prompts:factor_hypothesis_specification").r(),
50:         }
51:         return context_dict, True
52: 
53:     def convert_response(self, response: str) -> Hypothesis:
54:         response_dict = json.loads(response)
55:         hypothesis = QlibFactorHypothesis(
56:             hypothesis=response_dict.get("hypothesis"),
57:             reason=response_dict.get("reason"),
58:             concise_reason=response_dict.get("concise_reason"),
59:             concise_observation=response_dict.get("concise_observation"),
60:             concise_justification=response_dict.get("concise_justification"),
61:             concise_knowledge=response_dict.get("concise_knowledge"),
62:         )
63:         return hypothesis
64: 
65: 
66: class QlibFactorHypothesis2Experiment(FactorHypothesis2Experiment):
...
100:         response_dict = json.loads(response)
101:         tasks = []
102: 
103:         for factor_name in response_dict:
104:             description = response_dict[factor_name]["description"]
105:             formulation = response_dict[factor_name]["formulation"]
106:             variables = response_dict[factor_name]["variables"]
107:             tasks.append(
108:                 FactorTask(
109:                     factor_name=factor_name,
110:                     factor_description=description,
111:                     factor_formulation=formulation,
112:                     variables=variables,
113:                 )
114:             )
115: 
116:         exp = QlibFactorExperiment(tasks, hypothesis=hypothesis)
117:         exp.based_experiments = [QlibFactorExperiment(sub_tasks=[])] + [
118:             t[0] for t in trace.hist if t[1] and isinstance(t[0], FactorExperiment)
119:         ]
120: 
121:         unique_tasks = []
122:         for task in tasks:
123:             duplicate = False
124:             for based_exp in exp.based_experiments:
125:                 if isinstance(based_exp, QlibModelExperiment):
126:                     continue
127:                 for sub_task in based_exp.sub_tasks:
128:                     if task.factor_name == sub_task.factor_name:
129:                         duplicate = True
130:                         break
131:                 if duplicate:
132:                     break
133:             if not duplicate:
134:                 unique_tasks.append(task)
135: 
136:         exp.tasks = unique_tasks
137:         return exp
...
```

## rdagent/scenarios/qlib/developer/factor_runner.py

```text
30:     Everything in a folder
31:     - config.yaml
32:     - price-volume data dumper
33:     - `data.py` + Adaptor to Factor implementation
34:     - results in `mlflow`
35:     """
36: 
37:     def calculate_information_coefficient(
38:         self, concat_feature: pd.DataFrame, SOTA_feature_column_size: int, new_feature_columns_size: int
39:     ) -> pd.DataFrame:
40:         res = pd.Series(index=range(SOTA_feature_column_size * new_feature_columns_size))
41:         for col1 in range(SOTA_feature_column_size):
42:             for col2 in range(SOTA_feature_column_size, SOTA_feature_column_size + new_feature_columns_size):
43:                 res.loc[col1 * new_feature_columns_size + col2 - SOTA_feature_column_size] = concat_feature.iloc[
44:                     :, col1
45:                 ].corr(concat_feature.iloc[:, col2])
46:         return res
47: 
48:     def deduplicate_new_factors(self, SOTA_feature: pd.DataFrame, new_feature: pd.DataFrame) -> pd.DataFrame:
49:         # calculate the IC between each column of SOTA_feature and new_feature
50:         # if the IC is larger than a threshold, remove the new_feature column
51:         # return the new_feature
52: 
53:         concat_feature = pd.concat([SOTA_feature, new_feature], axis=1)
54:         IC_max = (
55:             concat_feature.groupby("datetime")
56:             .parallel_apply(
57:                 lambda x: self.calculate_information_coefficient(x, SOTA_feature.shape[1], new_feature.shape[1])
58:             )
59:             .mean()
60:         )
61:         IC_max.index = pd.MultiIndex.from_product([range(SOTA_feature.shape[1]), range(new_feature.shape[1])])
62:         IC_max = IC_max.unstack().max(axis=0)
63:         return new_feature.iloc[:, IC_max[IC_max < 0.99].index]
64: 
65:     def cache_settings(self, exp: QlibFactorExperiment) -> str:
66:         fbps = FactorBasePropSetting()
67:         dates = [fbps.train_start, fbps.train_end, fbps.valid_start, fbps.valid_end, fbps.test_start, fbps.test_end]
68:         dates += [fbps.market, fbps.benchmark]
69:         # The qrun templates hold the model / backtest settings, so editing them must invalidate cached results.
...
135:                 combined_factors = pd.concat([SOTA_factor, new_factors], axis=1).dropna()
136:             else:
137:                 combined_factors = new_factors
138: 
139:             # Sort and nest the combined factors under 'feature'
140:             combined_factors = combined_factors.sort_index()
141:             combined_factors = combined_factors.loc[:, ~combined_factors.columns.duplicated(keep="last")]
142:             new_columns = pd.MultiIndex.from_product([["feature"], combined_factors.columns])
143:             combined_factors.columns = new_columns
144:             logger.info(f"Factor data processing completed.")
145: 
146:             num_features = len(exp.base_features) + len(combined_factors.columns)
147: 
148:             # Due to the rdagent and qlib docker image in the numpy version of the difference,
149:             # the `combined_factors_df.pkl` file could not be loaded correctly in qlib dokcer,
150:             # so we changed the file type of `combined_factors_df` from pkl to parquet.
151:             target_path = exp.experiment_workspace.workspace_path / "combined_factors_df.parquet"
152: 
153:             # Save the combined factors to the workspace
154:             combined_factors.to_parquet(target_path, engine="pyarrow")
155: 
156:             # If model exp exists in the previous experiment
157:             exist_sota_model_exp = False
158:             for base_exp in reversed(exp.based_experiments):
159:                 if isinstance(base_exp, QlibModelExperiment):
160:                     sota_model_exp = base_exp
161:                     exist_sota_model_exp = True
162:                     break
163:             logger.info(f"Experiment execution ...")
164:             if exist_sota_model_exp:
165:                 exp.experiment_workspace.inject_files(
166:                     **{"model.py": sota_model_exp.sub_workspace_list[0].file_dict["model.py"]}
...
```

## rdagent/scenarios/qlib/prompts.yaml

```text
1: hypothesis_and_feedback: |-
2:   =========================================================
3:   {% for experiment, feedback in trace.hist %}
4:   # Trial {{ loop.index }}: 
5:   ## Hypothesis
6:   {{ experiment.hypothesis }}
7:   ## Specific task: 
8:   {% for task in experiment.sub_tasks %}
9:   {% if task is not none and task.get_task_brief_information is defined %}
10:   {{ task.get_task_brief_information() }}
11:   {% endif %}
12:   {% endfor %}
13:   ## Backtest Analysis and Feedback:
14:   {% if experiment.result is not none %}
15:   Backtest Result: {{ experiment.result.loc[["IC", "1day.excess_return_without_cost.annualized_return", "1day.excess_return_without_cost.max_drawdown"]] }}
16:   {% endif %}
17:   Observation: {{ feedback.observations }}
18:   Hypothesis Evaluation: {{ feedback.hypothesis_evaluation }}
19:   Decision (Whether the hypothesis was successful): {{ feedback.decision }}
20:   =========================================================
21:   {% endfor %}
22: 
23: last_hypothesis_and_feedback: |-
24:   ## Hypothesis
25:   {{ experiment.hypothesis }}
26:   ## Specific task: 
27:   {% for task in experiment.sub_tasks %}
28:   {% if task is not none and task.get_task_brief_information is defined %}
29:   {{ task.get_task_brief_information() }}
30:   {% endif %}
31:   {% endfor %}
32:   ## Backtest Analysis and Feedback:
33:   {% if experiment.result is not none %}
34:   Backtest Result: {{ experiment.result.loc[["IC", "1day.excess_return_without_cost.annualized_return", "1day.excess_return_without_cost.max_drawdown"]] }}
35:   {% endif %}
36:   Training Log: 
37:   Here, you need to focus on analyzing whether there are any issues with the training. If any problems are identified, you must correct them in the next iteration and clearly describe how the changes will be made in the hypothesis.
38:   {{ experiment.stdout }} 
39:   Observation: {{ feedback.observations }}
40:   Evaluation: {{ feedback.hypothesis_evaluation }}
41:   Decision (Whether this experiment is SOTA): {{ feedback.decision }}
42:   New Hypothesis (Given in feedback stage, just for reference, and can be accepted or rejected in the next round): {{ feedback.new_hypothesis }}
43:   Reasoning (Justification for the new hypothesis): {{ feedback.reason }}
...
90:   5. Focus exclusively on the architecture of PyTorch models. Each hypothesis should specifically address architectural decisions, such as layer configurations, activation functions, regularization methods, and overall model structure. DO NOT do any feature-specific processing. Instead, you can propose innovative transformations on the input time-series data to enhance model training effectiveness.
91:   6. Avoid including aspects unrelated to architecture, such as input features or optimization strategies.
92:   7. Sometimes, when training performance is poor, adjusting hyperparameters can also be an effective strategy for improvement.
93:   8. Use standard libraries for baseline models, but also explore custom architecture designs to investigate novel structures. After sufficient trials with traditional models, aim for innovation comparable to top-tier AI conferences (NeurIPS, ICLR, ICML, SIGKDD, etc.) in time series modeling.
94: 
95: factor_hypothesis_specification: |-
96:   1. **1-5 Factors per Generation:**
97:     - Ensure each generation produces 1-5 factors.
98:     - Balance simplicity and complexity to build a robust factor library.
99:     - Make full use of the financial data provided to you instead of focusing solely on a specific field.
100:   2. **Simple and Effective Factors First:**
101:     - Start with factors that are simple, easy to achieve and likely effective.
102:     - Concisely explain why these factors are expected to work.
103:     - Avoid complex or combined factors initially.
104:   3. **Gradual Complexity Increase:**
105:     - Introduce more complex factors (e.g. factors that combine several raw data fields, conditional or regime-dependent definitions, cross-sectional transforms) as more experimental results are gathered.
106:     - Every factor must be a deterministic formula of the raw data. Do NOT train machine learning models (Ridge, LightGBM, random forest, etc.) inside the factor code: the downstream LightGBM already learns combinations, and per-day model fitting exceeds the execution time limit.
107:     - Combine factors only after simpler ones are tested and validated.
108:   4. **New Directions and Optimizations:**
109:     - If multiple consecutive iterations fail to produce factors surpassing SOTA, consider switching to a new direction and can starting with simple factors again.
110:     - If optimizing a specific type of factor, proceed from simple to complex.
111:   5. Note
112:     - Highlight that factors surpassing SOTA are included in the library to avoid re-implementation.
113:     - No matter how many factors you plan to generate, only reply with one set of hypothesis and reason. The hypothesis can include the proposal of multiple factors at the same time.
114: 
115: factor_experiment_output_format: |-
...
170: 
171:     {{ scenario }}
172:     
173:     You will receive a hypothesis, multiple tasks with their factors, their results, and the SOTA result. 
174:     Your feedback should specify whether the current result supports or refutes the hypothesis, compare it with previous SOTA (State of the Art) results, and suggest improvements or new directions.
175:     
176:     Please understand the following operation logic and then make your feedback that is suitable for the scenario:
177:       1. Logic Explanation:
178:         a) All factors that have surpassed SOTA in previous attempts will be included in the SOTA factor library.
179:         b) New experiments will generate new factors, which will be combined with the factors in the SOTA library.
180:         c) These combined factors will be backtested and compared against the current SOTA to enable continuous iteration.
181:       2. Development Directions:
182:         a) New Direction: Propose a new factor direction for exploration and development.
183:         b) Optimization of Existing Direction:
184:           - Suggest further improvements to that factor (this can include further optimization of the factor or proposing a direction that combines better with the factor).
185:           - Avoid re-implementing previous factors as those that surpassed SOTA are already included in the factor library and will be used in each run.
186:       3. Final Goal: To continuously accumulate factors that surpass each iteration to maintain the best SOTA.
187:     
188:     When judging the results:
189:       1. The SOTA decision is driven by IC: recommend replacing the best result only if the combined IC improves clearly (by at least about 0.0005) and Rank IC does not decrease. The final decision is made by this rule.
190:       2. Annualized return and max drawdown come from a single backtest and vary by several percentage points between model random seeds; treat them as secondary evidence and do not accept or reject on them alone.
191:       3. Explain which new factor most plausibly drove any IC change, and use that to guide the next hypothesis.
192: 
193:     Consider Changing Direction for Significant Gaps with SOTA:
194:       - If the new results significantly differ from the SOTA, consider exploring a new direction (write new type factors).
195:       - Avoid re-implementing previous factors as those that surpassed SOTA are already included in the factor library and will be used in each run.
196: 
197:     Please provide detailed and constructive feedback for future exploration.
198:     Respond in JSON format. Example JSON structure for Result Analysis:
199:     {
200:       "Observations": "Your overall observations here",
201:       "Feedback for Hypothesis": "Observations related to the hypothesis",
202:       "New Hypothesis": "Your new hypothesis here",
203:       "Reasoning": "Reasoning for the new hypothesis",
204:       "Replace Best Result": "yes or no"
205:     }
206:   user: |-
207:     Target hypothesis: 
208:     {{ hypothesis_text }}
209:     Tasks and Factors:
210:     {% for task in task_details %}
211:       - {{ task.factor_name }}: {{ task.factor_description }}
212:         - Factor Formulation: {{ task.factor_formulation }}
213:         - Variables: {{ task.variables }}
214:         - Factor Implementation: {{ task.factor_implementation }}
215:         {% if task.factor_implementation == "False" %}
216:         **Note: This factor was not implemented in the current experiment. Only the hypothesis for implemented factors can be verified.**
217:         {% endif %}
218:     {% endfor %}
219:     Combined Results: 
220:     {{ combined_result }}
221:     
222:     Analyze the combined result in the context of its ability to:
223:     1. Support or refute the hypothesis.
224:     2. Show improvement or deterioration compared to the SOTA experiment.
225:     
226:     Note: Only factors with 'Factor Implementation' as True are implemented and tested in this experiment. If 'Factor Implementation' is False, the hypothesis for that factor cannot be verified in this run.
227: 
228: model_feedback_generation:
229:   system: |-
...
```

## rdagent/components/proposal/__init__.py

```text
30:         self,
31:         trace: Trace,
32:         plan: ExperimentPlan | None = None,
33:     ) -> Hypothesis:
34:         context_dict, json_flag = self.prepare_context(trace)
35: 
36:         system_prompt = T(".prompts:hypothesis_gen.system_prompt").r(
37:             targets=self.targets,
38:             scenario=(
39:                 self.scen.get_scenario_all_desc(filtered_tag=self.targets)
40:                 if self.targets in ["factor", "model"]
41:                 else self.scen.get_scenario_all_desc(filtered_tag="hypothesis_and_experiment")
42:             ),
43:             hypothesis_output_format=context_dict["hypothesis_output_format"],
44:             hypothesis_specification=context_dict["hypothesis_specification"],
45:             user_instruction=plan.get("user_instruction", None) if plan is not None else None,
46:         )
47:         user_prompt = T(".prompts:hypothesis_gen.user_prompt").r(
48:             targets=self.targets,
49:             hypothesis_and_feedback=context_dict["hypothesis_and_feedback"],
50:             last_hypothesis_and_feedback=(
51:                 context_dict["last_hypothesis_and_feedback"] if "last_hypothesis_and_feedback" in context_dict else ""
52:             ),
53:             sota_hypothesis_and_feedback=(
54:                 context_dict["sota_hypothesis_and_feedback"] if "sota_hypothesis_and_feedback" in context_dict else ""
55:             ),
56:             RAG=context_dict["RAG"],
57:         )
58: 
59:         resp = APIBackend().build_messages_and_create_chat_completion(
60:             user_prompt, system_prompt, json_mode=json_flag, json_target_type=dict[str, str]
61:         )
62: 
63:         hypothesis = self.convert_response(resp)
64: 
65:         return hypothesis
66: 
67: 
68: class FactorHypothesisGen(LLMHypothesisGen):
69:     def __init__(self, scen: Scenario):
70:         super().__init__(scen)
71:         self.targets = "factors"
72: 
73: 
74: class ModelHypothesisGen(LLMHypothesisGen):
75:     def __init__(self, scen: Scenario):
...
```
