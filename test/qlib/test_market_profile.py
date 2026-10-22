from pathlib import Path

import pytest
import yaml
from jinja2 import Environment

from rdagent.app.qlib_rd_loop.conf import FactorBasePropSetting
from rdagent.scenarios.qlib.developer.utils import market_profile_env

EXPERIMENT = Path(__file__).resolve().parents[2] / "rdagent" / "scenarios" / "qlib" / "experiment"
TEMPLATES = sorted(EXPERIMENT.glob("*_template/conf_*.yaml"))
US_PROFILE = EXPERIMENT.parent / "market_profiles" / "us_sp500.profile"


def render(path: Path, **context: str) -> dict:
    context.setdefault("feature_expressions", "['$close']")
    context.setdefault("feature_names", "['CLOSE']")
    return yaml.safe_load(Environment().from_string(path.read_text()).render(**context))


def exchange(conf: dict) -> dict:
    return conf["port_analysis_config"]["backtest"]["exchange_kwargs"]


@pytest.mark.offline
def test_templates_keep_a_share_defaults_without_profile() -> None:
    assert len(TEMPLATES) == 5
    for path in TEMPLATES:
        conf = render(path)
        assert conf["qlib_init"] == {"provider_uri": "~/.qlib/qlib_data/cn_data", "region": "cn"}, path.name
        assert exchange(conf) == {
            "limit_threshold": 0.095,
            "deal_price": "close",
            "open_cost": 0.0005,
            "close_cost": 0.0015,
            "min_cost": 5,
        }, path.name


@pytest.mark.offline
def test_us_profile_renders_us_market(monkeypatch) -> None:
    for line in US_PROFILE.read_text().splitlines():
        if line and not line.startswith("#"):
            key, value = line.split("=", 1)
            monkeypatch.setenv(key, value.strip('"'))
    settings = FactorBasePropSetting()
    profile = market_profile_env(settings)
    assert profile["limit_threshold"] == "null"

    for path in TEMPLATES:
        conf = render(path, market=settings.market, benchmark=settings.benchmark, **profile)
        assert conf["qlib_init"] == {"provider_uri": "~/.qlib/qlib_data/us_data", "region": "us"}, path.name
        assert conf["market"] == "sp500" and conf["benchmark"] == "^SP500TR", path.name
        assert exchange(conf) == {
            "limit_threshold": None,
            "deal_price": "close",
            "open_cost": 0.0005,
            "close_cost": 0.0005,
            "min_cost": 0.0,
        }, path.name
