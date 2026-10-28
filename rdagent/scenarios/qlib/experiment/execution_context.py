"""Describe the executor's existing protocol; do not change it to match a prompt."""

import json
from pathlib import Path

import yaml
from jinja2 import Template

from rdagent.app.qlib_rd_loop.conf import FactorBasePropSetting, ModelBasePropSetting
from rdagent.utils.qlib import ALPHA20


def describe_execution(action: str, features: dict[str, str] | None = None) -> str:
    settings = FactorBasePropSetting() if action == "factor" else ModelBasePropSetting()
    source = Path(__file__).parent / (
        "factor_template/conf_baseline.yaml"
        if action == "factor"
        else "model_template/conf_baseline_factors_model.yaml"
    )
    render_features = ALPHA20 if features is None else features
    values = {
        field: getattr(settings, field)
        for field in (
            "market",
            "benchmark",
            "train_start",
            "train_end",
            "valid_start",
            "valid_end",
            "test_start",
            "test_end",
        )
    }
    values.update(
        feature_names=repr(list(render_features)),
        feature_expressions=repr(list(render_features.values())),
        num_features=len(render_features),
        n_epochs=100,
        lr=2e-4,
        early_stop=10,
        batch_size=256,
        weight_decay=0.0001,
    )
    config = yaml.safe_load(Template(source.read_text()).render(**values))
    handler = config["data_handler_config"]
    summary = {
        "branch": action,
        "market": settings.market,
        "benchmark": settings.benchmark,
        "segments": config["task"]["dataset"]["kwargs"]["segments"],
        "label": handler["data_loader"]["kwargs"]["dataloader_l"][0]["kwargs"]["config"]["label"],
        "infer_processors": handler["infer_processors"],
        "learn_processors": handler["learn_processors"],
        "baseline_model": config["task"]["model"],
        "portfolio": config["port_analysis_config"],
    }
    if features is not None:
        summary["runtime_base_features"] = features
    else:
        summary["base_features"] = (
            "Supplied by the execution plan (Alpha20 by default); accepted factors are added by the runner."
        )
    if action == "model":
        summary["baseline_model_note"] = (
            "Architecture and training hyperparameters are supplied by the model task; listed defaults are not fixed sample counts."
        )
    return "Actual execution protocol (from current settings and repository templates):\n" + json.dumps(
        summary, default=str, ensure_ascii=False, indent=2
    )
