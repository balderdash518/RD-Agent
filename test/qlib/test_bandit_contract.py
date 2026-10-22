"""Offline regression tests for the metrics and posterior used by the real scheduler."""

from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from rdagent.scenarios.qlib.proposal.bandit import (
    LinearThompsonTwoArm,
    extract_metrics_from_experiment,
)

pytestmark = pytest.mark.offline


@pytest.mark.parametrize("as_frame", [False, True])
def test_actual_qlib_arr_and_drawdown_are_read_without_silent_zero(as_frame):
    result = pd.Series(
        {
            "IC": 0.02,
            "ICIR": 0.1,
            "Rank IC": 0.03,
            "Rank ICIR": 0.2,
            "1day.excess_return_with_cost.annualized_return": 0.0864520568747058,
            "1day.excess_return_with_cost.information_ratio": 0.8,
            "1day.excess_return_with_cost.max_drawdown": -0.15,
        }
    )
    metric = extract_metrics_from_experiment(SimpleNamespace(result=result.to_frame() if as_frame else result))
    assert metric.arr == pytest.approx(0.0864520568747058)
    # Qlib drawdowns are negative: a smaller loss must have a larger utility.
    assert metric.as_vector()[6] == pytest.approx(-0.15)
    assert metric.calmar == pytest.approx(metric.arr / 0.15)


def test_missing_or_nonfinite_results_produce_a_finite_context():
    for result in (None, pd.Series({"IC": np.nan, "Rank IC": np.inf})):
        metric = extract_metrics_from_experiment(SimpleNamespace(result=result))
        assert np.isfinite(metric.as_vector()).all()


def test_sequential_posterior_equals_closed_form_bayesian_regression():
    bandit = LinearThompsonTwoArm(dim=2, prior_var=10.0, noise_var=0.5)
    xs = np.array([[1.0, 2.0], [3.0, -1.0], [-0.5, 1.0]])
    rewards = np.array([0.3, -0.1, 0.8])
    for x, reward in zip(xs, rewards):
        bandit.update("factor", x, reward)
    expected_precision = np.eye(2) / 10.0 + xs.T @ xs / 0.5
    expected_mean = np.linalg.solve(expected_precision, xs.T @ rewards / 0.5)
    np.testing.assert_allclose(bandit.precision["factor"], expected_precision)
    np.testing.assert_allclose(bandit.mean["factor"], expected_mean)
    np.testing.assert_array_equal(bandit.mean["model"], np.zeros(2))
