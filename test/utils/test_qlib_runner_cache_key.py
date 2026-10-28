import pytest

from rdagent.scenarios.qlib.developer.factor_runner import QlibFactorRunner
from rdagent.scenarios.qlib.experiment.factor_experiment import QlibFactorExperiment


@pytest.mark.offline
def test_backtest_cache_key_tracks_dates_and_base_features(monkeypatch) -> None:
    runner = QlibFactorRunner(scen=None)
    exp = QlibFactorExperiment(sub_tasks=[])
    exp.base_features = {"RESI5": "Resi($close, 5)/$close"}

    monkeypatch.setenv("QLIB_FACTOR_TEST_END", "2020-08-01")
    default_key = runner.get_cache_key(exp)
    assert runner.get_cache_key(exp) == default_key
    assert QlibFactorRunner.cache_key(runner, exp) == default_key

    monkeypatch.setenv("QLIB_FACTOR_TEST_END", "2018-12-31")
    assert runner.get_cache_key(exp) != default_key

    monkeypatch.setenv("QLIB_FACTOR_TEST_END", "2020-08-01")
    exp.base_features = {"KLEN": "($high-$low)/$open"}
    assert runner.get_cache_key(exp) != default_key


@pytest.mark.offline
def test_backtest_cache_key_tracks_qlib_data(monkeypatch) -> None:
    from rdagent.scenarios.qlib.developer import factor_runner

    runner = QlibFactorRunner(scen=None)
    exp = QlibFactorExperiment(sub_tasks=[])
    monkeypatch.setattr(factor_runner, "qlib_data_fingerprint", lambda: "day.txt:1:1")
    old_data_key = runner.get_cache_key(exp)
    monkeypatch.setattr(factor_runner, "qlib_data_fingerprint", lambda: "day.txt:2:2")

    assert runner.get_cache_key(exp) != old_data_key


@pytest.mark.offline
def test_backtest_cache_key_tracks_market(monkeypatch) -> None:
    runner = QlibFactorRunner(scen=None)
    exp = QlibFactorExperiment(sub_tasks=[])
    monkeypatch.setenv("QLIB_FACTOR_MARKET", "csi300")
    monkeypatch.setenv("QLIB_FACTOR_BENCHMARK", "SH000300")
    csi300_key = runner.get_cache_key(exp)
    monkeypatch.setenv("QLIB_FACTOR_MARKET", "csi500")
    monkeypatch.setenv("QLIB_FACTOR_BENCHMARK", "SH000905")

    assert runner.get_cache_key(exp) != csi300_key
