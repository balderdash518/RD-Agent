import re
from pathlib import Path
from typing import Any

import pandas as pd

from rdagent.components.coder.model_coder.conf import MODEL_COSTEER_SETTINGS
from rdagent.core.experiment import FBWorkspace
from rdagent.log import rdagent_logger as logger
from rdagent.utils.env import QlibCondaConf, QlibCondaEnv, QTDockerEnv

# Lines of the qrun output that describe how the model trained. The raw output is mostly progress bars, so only these
# are kept as the experiment's training log (shown to the LLM as "Training Log").
_TRAINING_SCORE = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"
_TRAINING_LOG_PATTERN = re.compile(
    "|".join(
        [
            # PyTorch models: per-epoch scores and the selected epoch.
            rf"Epoch\d+: train {_TRAINING_SCORE}, valid {_TRAINING_SCORE}",
            rf"best score: {_TRAINING_SCORE} @ \d+ epoch",
            # LightGBM: early-stopping setup, the selected iteration with its scores, and the periodic eval lines.
            r"Training until validation scores don't improve for \d+ rounds",
            r"(?:Early stopping, best iteration is|Did not meet early stopping\. Best iteration is):\r?\n\[\d+\][^\n]*",
            r"^\[\d+\]\t[^\n]*",
        ]
    ),
    re.MULTILINE,
)


def extract_training_log(qlib_log: str) -> str:
    """Keep only the training-progress lines of a qrun log (PyTorch epochs or LightGBM iterations)."""
    return "\n".join(m.group(0).strip() for m in _TRAINING_LOG_PATTERN.finditer(qlib_log))


class QlibFBWorkspace(FBWorkspace):
    def __init__(self, template_folder_path: Path, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.inject_code_from_folder(template_folder_path)

    def execute(self, qlib_config_name: str = "conf.yaml", run_env: dict = {}, *args, **kwargs) -> str:
        if MODEL_COSTEER_SETTINGS.env_type == "docker":
            qtde = QTDockerEnv()
        elif MODEL_COSTEER_SETTINGS.env_type == "conda":
            qtde = QlibCondaEnv(conf=QlibCondaConf())
        else:
            logger.error(f"Unknown env_type: {MODEL_COSTEER_SETTINGS.env_type}")
            return None, "Unknown environment type"
        qtde.prepare()

        # Run the Qlib backtest
        execute_qlib_log = qtde.check_output(
            local_path=str(self.workspace_path),
            entry=f"qrun {qlib_config_name}",
            env=run_env,
        )
        logger.log_object(execute_qlib_log, tag="Qlib_execute_log")

        execute_log = qtde.check_output(
            local_path=str(self.workspace_path),
            entry="python read_exp_res.py",
            env=run_env,
        )

        quantitative_backtesting_chart_path = self.workspace_path / "ret.parquet"
        if quantitative_backtesting_chart_path.exists():
            ret_df = pd.read_parquet(quantitative_backtesting_chart_path)
            logger.log_object(ret_df, tag="Quantitative Backtesting Chart")
        else:
            logger.error("No result file found.")
            return None, execute_qlib_log

        qlib_res_path = self.workspace_path / "qlib_res.csv"
        if qlib_res_path.exists():
            # Here, we ensure that the qlib experiment has run successfully before extracting information from execute_qlib_log using regex; otherwise, we keep the original experiment stdout.
            execute_qlib_log = extract_training_log(execute_qlib_log)
            return pd.read_csv(qlib_res_path, index_col=0).iloc[:, 0], execute_qlib_log
        else:
            logger.error(f"File {qlib_res_path} does not exist.")
            return None, execute_qlib_log
