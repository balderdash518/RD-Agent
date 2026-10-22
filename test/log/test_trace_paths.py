from pathlib import Path

from rdagent.log.ui.trace_paths import discover_trace_folders


def make_trace(root: Path, name: str) -> Path:
    trace = root / name
    scenario = trace / "scenario" / "123"
    scenario.mkdir(parents=True)
    (scenario / "2026-10-20_15-26-20-726733.pkl").touch()
    return trace


def test_lists_newest_sessions_and_ignores_empty_cli_logs(tmp_path):
    make_trace(tmp_path, "2026-10-19_15-00-00-000000")
    make_trace(tmp_path, "2026-10-20_15-26-09-427246")
    (tmp_path / "2026-10-21_empty").mkdir()
    debug = tmp_path / "2026-10-22_debug"
    debug.mkdir()
    (debug / "debug_llm.pkl").touch()

    assert discover_trace_folders(tmp_path) == [
        Path("2026-10-20_15-26-09-427246"),
        Path("2026-10-19_15-00-00-000000"),
    ]


def test_accepts_a_single_session_as_the_log_root(tmp_path):
    trace = make_trace(tmp_path, "run")
    assert discover_trace_folders(trace) == [Path(".")]


def test_empty_and_missing_roots(tmp_path):
    assert discover_trace_folders(tmp_path) == []
    assert discover_trace_folders(tmp_path / "missing") == []
