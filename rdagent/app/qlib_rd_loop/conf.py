from typing import Literal

from pydantic_settings import SettingsConfigDict
from rdagent.components.workflow.conf import BasePropSetting


class ModelBasePropSetting(BasePropSetting):
    model_config = SettingsConfigDict(env_prefix="QLIB_MODEL_", protected_namespaces=(), env_parse_none_str="None")

    # 1) override base settings
    scen: str = "rdagent.scenarios.qlib.experiment.model_experiment.QlibModelScenario"
    """Scenario class for Qlib Model"""

    hypothesis_gen: str = "rdagent.scenarios.qlib.proposal.model_proposal.QlibModelHypothesisGen"
    """Hypothesis generation class"""

    hypothesis2experiment: str = "rdagent.scenarios.qlib.proposal.model_proposal.QlibModelHypothesis2Experiment"
    """Hypothesis to experiment class"""

    coder: str = "rdagent.scenarios.qlib.developer.model_coder.QlibModelCoSTEER"
    """Coder class"""

    runner: str = "rdagent.scenarios.qlib.developer.model_runner.QlibModelRunner"
    """Runner class"""

    summarizer: str = "rdagent.scenarios.qlib.developer.feedback.QlibModelExperiment2Feedback"
    """Summarizer class"""

    evolving_n: int = 10
    """Number of evolutions"""

    train_start: str = "2010-01-01"
    """Start date of the training segment"""

    train_end: str = "2020-12-31"
    """End date of the training segment"""

    valid_start: str = "2021-01-01"
    """Start date of the validation segment"""

    valid_end: str = "2022-12-31"
    """End date of the validation segment"""

    test_start: str = "2023-01-01"
    """Start date of the test / backtest segment"""

    test_end: str | None = "2026-10-14"
    """End date of the test / backtest segment"""

    market: str = "csi500"
    """Qlib instrument universe, e.g. csi300 or csi500"""

    benchmark: str = "SH000905"
    """Benchmark index for excess returns, e.g. SH000300 or SH000905"""

    provider_uri: str = "~/.qlib/qlib_data/cn_data"
    """qlib data directory (~/.qlib is mounted at the same path in the qlib Docker image)"""

    region: str = "cn"
    """qlib region: cn or us"""

    limit_threshold: float | None = 0.095
    """Daily price-limit threshold; None for markets without price limits (e.g. US)"""

    deal_price: str = "close"
    """Price used to fill orders in the backtest"""

    open_cost: float = 0.0005
    """Buy cost rate"""

    close_cost: float = 0.0015
    """Sell cost rate (A-share default includes stamp duty)"""

    min_cost: float = 5
    """Minimum cost per trade, in account currency"""


class FactorBasePropSetting(BasePropSetting):
    model_config = SettingsConfigDict(env_prefix="QLIB_FACTOR_", protected_namespaces=(), env_parse_none_str="None")

    # 1) override base settings
    scen: str = "rdagent.scenarios.qlib.experiment.factor_experiment.QlibFactorScenario"
    """Scenario class for Qlib Factor"""

    hypothesis_gen: str = "rdagent.scenarios.qlib.proposal.factor_proposal.QlibFactorHypothesisGen"
    """Hypothesis generation class"""

    hypothesis2experiment: str = "rdagent.scenarios.qlib.proposal.factor_proposal.QlibFactorHypothesis2Experiment"
    """Hypothesis to experiment class"""

    coder: str = "rdagent.scenarios.qlib.developer.factor_coder.QlibFactorCoSTEER"
    """Coder class"""

    runner: str = "rdagent.scenarios.qlib.developer.factor_runner.QlibFactorRunner"
    """Runner class"""

    summarizer: str = "rdagent.scenarios.qlib.developer.feedback.QlibFactorExperiment2Feedback"
    """Summarizer class"""

    evolving_n: int = 10
    """Number of evolutions"""

    train_start: str = "2010-01-01"
    """Start date of the training segment"""

    train_end: str = "2020-12-31"
    """End date of the training segment"""

    valid_start: str = "2021-01-01"
    """Start date of the validation segment"""

    valid_end: str = "2022-12-31"
    """End date of the validation segment"""

    test_start: str = "2023-01-01"
    """Start date of the test / backtest segment"""

    test_end: str | None = "2026-10-14"
    """End date of the test / backtest segment"""

    market: str = "csi500"
    """Qlib instrument universe, e.g. csi300 or csi500"""

    benchmark: str = "SH000905"
    """Benchmark index for excess returns, e.g. SH000300 or SH000905"""

    provider_uri: str = "~/.qlib/qlib_data/cn_data"
    """qlib data directory (~/.qlib is mounted at the same path in the qlib Docker image)"""

    region: str = "cn"
    """qlib region: cn or us"""

    limit_threshold: float | None = 0.095
    """Daily price-limit threshold; None for markets without price limits (e.g. US)"""

    deal_price: str = "close"
    """Price used to fill orders in the backtest"""

    open_cost: float = 0.0005
    """Buy cost rate"""

    close_cost: float = 0.0015
    """Sell cost rate (A-share default includes stamp duty)"""

    min_cost: float = 5
    """Minimum cost per trade, in account currency"""

    acceptance_policy: Literal["llm", "ic"] = "llm"
    """Use repository LLM feedback by default; opt into the local IC gate explicitly."""

    accept_min_ic_gain: float = 0.0005
    """A new factor set replaces SOTA only if the combined test IC improves by at least this much"""

    accept_min_rank_ic_gain: float = 0.0
    """...and the combined test Rank IC improves by at least this much (0 = must not decrease)"""


class FactorFromReportPropSetting(FactorBasePropSetting):
    # 1) override the scen attribute
    scen: str = "rdagent.scenarios.qlib.experiment.factor_from_report_experiment.QlibFactorFromReportScenario"
    """Scenario class for Qlib Factor from Report"""

    # 2) sub task specific:
    report_result_json_file_path: str = "git_ignore_folder/report_list.json"
    """Path to the JSON file listing research reports for factor extraction"""

    max_factors_per_exp: int = 6
    """Maximum number of factors implemented per experiment"""

    report_limit: int = 20
    """Maximum number of reports to process"""


class QuantBasePropSetting(BasePropSetting):
    model_config = SettingsConfigDict(env_prefix="QLIB_QUANT_", protected_namespaces=(), env_parse_none_str="None")

    # 1) override base settings
    scen: str = "rdagent.scenarios.qlib.experiment.quant_experiment.QlibQuantScenario"
    """Scenario class for Qlib Model"""

    quant_hypothesis_gen: str = "rdagent.scenarios.qlib.proposal.quant_proposal.QlibQuantHypothesisGen"
    """Hypothesis generation class"""

    model_hypothesis2experiment: str = "rdagent.scenarios.qlib.proposal.model_proposal.QlibModelHypothesis2Experiment"
    """Hypothesis to experiment class"""

    model_coder: str = "rdagent.scenarios.qlib.developer.model_coder.QlibModelCoSTEER"
    """Coder class"""

    model_runner: str = "rdagent.scenarios.qlib.developer.model_runner.QlibModelRunner"
    """Runner class"""

    model_summarizer: str = "rdagent.scenarios.qlib.developer.feedback.QlibModelExperiment2Feedback"
    """Summarizer class"""

    factor_hypothesis2experiment: str = (
        "rdagent.scenarios.qlib.proposal.factor_proposal.QlibFactorHypothesis2Experiment"
    )
    """Hypothesis to experiment class"""

    factor_coder: str = "rdagent.scenarios.qlib.developer.factor_coder.QlibFactorCoSTEER"
    """Coder class"""

    factor_runner: str = "rdagent.scenarios.qlib.developer.factor_runner.QlibFactorRunner"
    """Runner class"""

    factor_summarizer: str = "rdagent.scenarios.qlib.developer.feedback.QlibFactorExperiment2Feedback"
    """Summarizer class"""

    evolving_n: int = 10
    """Number of evolutions"""

    action_selection: str = "bandit"
    """Action selection strategy: bandit, LLM, or random."""

    train_start: str = "2010-01-01"
    """Start date of the training segment"""

    train_end: str = "2020-12-31"
    """End date of the training segment"""

    valid_start: str = "2021-01-01"
    """Start date of the validation segment"""

    valid_end: str = "2022-12-31"
    """End date of the validation segment"""

    test_start: str = "2023-01-01"
    """Start date of the test / backtest segment"""

    test_end: str | None = "2026-10-14"
    """End date of the test / backtest segment"""

    market: str = "csi500"
    """Qlib instrument universe, e.g. csi300 or csi500"""

    benchmark: str = "SH000905"
    """Benchmark index for excess returns, e.g. SH000300 or SH000905"""

    provider_uri: str = "~/.qlib/qlib_data/cn_data"
    """qlib data directory (~/.qlib is mounted at the same path in the qlib Docker image)"""

    region: str = "cn"
    """qlib region: cn or us"""

    limit_threshold: float | None = 0.095
    """Daily price-limit threshold; None for markets without price limits (e.g. US)"""

    deal_price: str = "close"
    """Price used to fill orders in the backtest"""

    open_cost: float = 0.0005
    """Buy cost rate"""

    close_cost: float = 0.0015
    """Sell cost rate (A-share default includes stamp duty)"""

    min_cost: float = 5
    """Minimum cost per trade, in account currency"""


FACTOR_PROP_SETTING = FactorBasePropSetting()
FACTOR_FROM_REPORT_PROP_SETTING = FactorFromReportPropSetting()
MODEL_PROP_SETTING = ModelBasePropSetting()
QUANT_PROP_SETTING = QuantBasePropSetting()
