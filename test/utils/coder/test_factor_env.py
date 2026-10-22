import sys
from pathlib import Path

from rdagent.components.coder.factor_coder.config import get_factor_env
from rdagent.utils.env import CondaConf


def test_factor_env_falls_back_to_current_interpreter_without_conda(monkeypatch) -> None:
    monkeypatch.delenv("CONDA_DEFAULT_ENV", raising=False)

    env = get_factor_env()

    assert not isinstance(env.conf, CondaConf)
    assert env.conf.bin_path == str(Path(sys.executable).parent)
    assert env.conf.default_entry == "python main.py"


def test_qlib_docker_allows_mlflow_file_store() -> None:
    from rdagent.utils.env import QlibDockerConf

    assert QlibDockerConf().env_dict["MLFLOW_ALLOW_FILE_STORE"] == "true"
