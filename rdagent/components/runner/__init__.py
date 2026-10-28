import json
from copy import deepcopy

from rdagent.core.developer import Developer
from rdagent.core.experiment import ASpecificExp, Experiment
from rdagent.oai.llm_utils import md5_hash


class CachedRunner(Developer[ASpecificExp]):
    def cache_settings(self, exp: Experiment) -> str:
        """Run settings that change results without changing the tasks (e.g. backtest dates)."""
        return ""

    def get_cache_key(self, exp: Experiment) -> str:
        # Hash the implementation, not just its natural-language task. Dependencies form a DAG;
        # memoization avoids repeatedly expanding the entire accepted experiment history.
        memo: dict[int, str] = {}

        def identity(node: Experiment) -> str:
            if id(node) not in memo:
                payload = {
                    "type": f"{type(node).__module__}.{type(node).__qualname__}",
                    "tasks": [task.get_task_information() for task in node.sub_tasks],
                    "implementations": [getattr(ws, "file_dict", None) for ws in node.sub_workspace_list],
                    "base_features": getattr(node, "base_features", {}),
                    "base_feature_codes": getattr(node, "base_feature_codes", {}),
                    "dependencies": [identity(base) for base in node.based_experiments],
                }
                memo[id(node)] = md5_hash(json.dumps(payload, sort_keys=True, default=str))
            return memo[id(node)]

        return md5_hash(
            f"runner-v2:{type(self).__module__}.{type(self).__qualname__}:{identity(exp)}:{self.cache_settings(exp)}"
        )

    @staticmethod
    def cache_key(runner: "CachedRunner", exp: Experiment) -> str:
        """Hash function for `cache_with_pickle` that honours subclass overrides."""
        return runner.get_cache_key(exp)

    def assign_cached_result(self, exp: Experiment, cached_res: Experiment) -> Experiment:
        restored: set[int] = set()

        def restore(current: Experiment, cached: Experiment) -> None:
            if id(current) in restored:
                return
            restored.add(id(current))
            if len(current.based_experiments) != len(cached.based_experiments):
                raise ValueError("Cached experiment dependencies do not match the current experiment")
            for current_base, cached_base in zip(current.based_experiments, cached.based_experiments):
                restore(current_base, cached_base)
            current.result = deepcopy(cached.result)
            if hasattr(cached, "stdout"):
                current.stdout = cached.stdout

        restore(exp, cached_res)
        return exp
