import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import List, Literal, Tuple

import numpy as np


@dataclass
class Metrics:
    ic: float = 0.0
    icir: float = 0.0
    rank_ic: float = 0.0
    rank_icir: float = 0.0
    arr: float = 0.0
    ir: float = 0.0
    mdd: float = 0.0
    calmar: float = 0.0

    def as_vector(self) -> np.ndarray:
        return np.array(
            [
                self.ic,
                self.icir,
                self.rank_ic,
                self.rank_icir,
                self.arr,
                self.ir,
                -abs(self.mdd),
                self.calmar,
            ]
        )


def extract_metrics_from_experiment(experiment) -> Metrics:
    """Extract metrics from experiment feedback"""
    result = experiment.result
    if result is None:
        return Metrics()
    if getattr(result, "ndim", 1) == 2:
        result = result.iloc[:, 0]

    def finite_metric(key: str) -> float:
        value = float(result.get(key, 0.0))
        return value if np.isfinite(value) else 0.0

    arr = finite_metric("1day.excess_return_with_cost.annualized_return")
    mdd = finite_metric("1day.excess_return_with_cost.max_drawdown")
    calmar = arr / abs(mdd) if mdd else 0.0
    return Metrics(
        ic=finite_metric("IC"),
        icir=finite_metric("ICIR"),
        rank_ic=finite_metric("Rank IC"),
        rank_icir=finite_metric("Rank ICIR"),
        arr=arr,
        ir=finite_metric("1day.excess_return_with_cost.information_ratio"),
        mdd=mdd,
        calmar=calmar,
    )


class LinearThompsonTwoArm:
    def __init__(self, dim: int, prior_var: float = 1.0, noise_var: float = 1.0):
        self.dim = dim
        self.noise_var = noise_var
        # Each arm has its own posterior: mean & inverse of covariance (precision matrix)
        self.mean = {
            "factor": np.zeros(dim),
            "model": np.zeros(dim),
        }
        self.precision = {
            "factor": np.eye(dim) / prior_var,
            "model": np.eye(dim) / prior_var,
        }

    def sample_reward(self, arm: str, x: np.ndarray) -> float:
        P = self.precision[arm]
        P = 0.5 * (P + P.T)

        eps = 1e-6
        try:
            cov = np.linalg.inv(P + eps * np.eye(self.dim))
            L = np.linalg.cholesky(cov)
            z = np.random.randn(self.dim)
            w_sample = self.mean[arm] + L @ z
        except np.linalg.LinAlgError:
            w_sample = self.mean[arm]

        return float(np.dot(w_sample, x))

    def update(self, arm: str, x: np.ndarray, r: float) -> None:
        P = self.precision[arm]
        natural_mean = P @ self.mean[arm] + (r / self.noise_var) * x
        new_precision = P + np.outer(x, x) / self.noise_var
        self.precision[arm] = new_precision
        self.mean[arm] = np.linalg.solve(new_precision, natural_mean)

    def next_arm(self, x: np.ndarray) -> str:
        scores = {arm: self.sample_reward(arm, x) for arm in ("factor", "model")}
        return max(scores, key=scores.get)


class EnvController:
    def __init__(self, weights: Tuple[float, ...] = None) -> None:
        self.weights = np.asarray(weights or (0.1, 0.1, 0.05, 0.05, 0.25, 0.15, 0.1, 0.2))
        self.bandit = LinearThompsonTwoArm(dim=8, prior_var=10.0, noise_var=0.5)

    def reward(self, m: Metrics) -> float:
        return float(np.dot(self.weights, m.as_vector()))

    def decide(self, m: Metrics) -> str:
        x = m.as_vector()
        return self.bandit.next_arm(x)

    def record(self, m: Metrics, arm: str) -> None:
        r = self.reward(m)
        self.bandit.update(arm, m.as_vector(), r)
