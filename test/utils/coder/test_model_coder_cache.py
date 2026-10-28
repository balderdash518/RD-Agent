from __future__ import annotations

import pytest
from rdagent.components.coder.model_coder.conf import MODEL_COSTEER_SETTINGS
from rdagent.components.coder.model_coder.model import ModelFBWorkspace


@pytest.mark.offline
def test_model_execution_cache_key_tracks_runtime_and_graph_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = ModelFBWorkspace()
    workspace.file_dict = {"model.py": "model_cls = object"}

    monkeypatch.setattr(MODEL_COSTEER_SETTINGS, "env_type", "conda")
    conda_key = workspace.hash_func(num_edges=20)

    monkeypatch.setattr(MODEL_COSTEER_SETTINGS, "env_type", "docker")
    docker_key = workspace.hash_func(num_edges=20)
    different_graph_key = workspace.hash_func(num_edges=21)

    assert conda_key != docker_key
    assert docker_key != different_graph_key
