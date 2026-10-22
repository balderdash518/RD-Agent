import hashlib
import re
import shutil
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from jinja2 import Environment, StrictUndefined
from rdagent.app.qlib_rd_loop.conf import FactorBasePropSetting
from rdagent.components.coder.factor_coder.config import FACTOR_COSTEER_SETTINGS
from rdagent.utils.env import QTDockerEnv


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _decode_hdf_labels(values: Any) -> list[str]:
    return [value.decode("utf-8") if isinstance(value, bytes) else str(value) for value in values]


@lru_cache(maxsize=8)
def _cached_hdf_profile(path_str: str, size_bytes: int, modified_ns: int) -> dict[str, Any]:
    """Read identity and coverage without materialising a large fixed-format HDF frame."""

    del size_bytes, modified_ns  # They intentionally form the cache key.
    path = Path(path_str)
    profile: dict[str, Any] = {
        "file_name": path.name,
        "sha256": _sha256_file(path),
        "size_bytes": path.stat().st_size,
    }
    try:
        with pd.HDFStore(path, mode="r") as store:
            key = store.keys()[0]
            storer = store.get_storer(key)
            group = storer.group
            children = group._v_children  # noqa: SLF001 - required for metadata-only reads of fixed HDF files
            values_node = children["block0_values"]
            columns = _decode_hdf_labels(children["axis0"].read())
            profile.update({"hdf_key": key, "rows": int(values_node.shape[0]), "columns": columns})

            if "axis1_level0" in children and "axis1_label0" in children and values_node.shape[0]:
                date_levels = children["axis1_level0"]
                date_labels = children["axis1_label0"]
                first_date = pd.Timestamp(date_levels[int(date_labels[0])])
                last_date = pd.Timestamp(date_levels[int(date_labels[-1])])
                profile.update({"date_start": first_date.isoformat(), "date_end": last_date.isoformat()})
            if "axis1_label1" in children:
                instrument_labels = children["axis1_label1"].read()
                profile["instruments"] = int(np.unique(instrument_labels).size)
    except (KeyError, IndexError, TypeError, ValueError):
        # Unexpected HDF layouts are rare for RD-Agent generated files. The
        # fallback preserves observability without making data loading fail.
        frame = pd.read_hdf(path)
        profile.update(_dataframe_profile(frame))
    return profile


def get_hdf_file_profile(path: str | Path) -> dict[str, Any]:
    """Return a stable, compact profile suitable for prompts and trace metadata."""

    resolved = Path(path).expanduser().resolve()
    stat = resolved.stat()
    return dict(_cached_hdf_profile(str(resolved), stat.st_size, stat.st_mtime_ns))


def _dataframe_profile(frame: pd.DataFrame) -> dict[str, Any]:
    profile: dict[str, Any] = {
        "rows": len(frame),
        "columns": [str(column) for column in frame.columns],
        "duplicate_index_rows": int(frame.index.duplicated().sum()),
        "missing_rate": {str(column): round(float(rate), 6) for column, rate in frame.isna().mean().items()},
    }
    if isinstance(frame.index, pd.MultiIndex):
        profile["index_names"] = [str(name) for name in frame.index.names]
        if "datetime" in frame.index.names and len(frame):
            dates = frame.index.get_level_values("datetime")
            profile.update(
                {
                    "date_start": pd.Timestamp(dates.min()).isoformat(),
                    "date_end": pd.Timestamp(dates.max()).isoformat(),
                },
            )
        if "instrument" in frame.index.names:
            profile["instruments"] = int(frame.index.get_level_values("instrument").nunique())
    return profile


def _profile_markdown(profile: dict[str, Any]) -> str:
    lines = [
        f"- Rows: {profile.get('rows', 'unknown')}",
        f"- Columns: {len(profile.get('columns', []))}",
        f"- Date range: {profile.get('date_start', 'unknown')} to {profile.get('date_end', 'unknown')}",
        f"- Instruments: {profile.get('instruments', 'unknown')}",
    ]
    if profile.get("sha256"):
        lines.append(f"- Dataset SHA-256: {profile['sha256']}")
    if "duplicate_index_rows" in profile:
        lines.append(f"- Duplicate index rows: {profile['duplicate_index_rows']}")
    missing = profile.get("missing_rate") or {}
    if missing:
        rates = ", ".join(f"{name}={rate:.4%}" for name, rate in sorted(missing.items()))
        lines.append("- Missing rate by column: " + rates)
    return "\n".join(lines)


def generate_data_folder_from_qlib():
    template_path = Path(__file__).parent / "factor_data_template"
    # Remove previous outputs so a failed generate.py cannot leave another dataset's files to be copied.
    for stale in ("daily_pv_all.h5", "daily_pv_debug.h5"):
        (template_path / stale).unlink(missing_ok=True)
    qtde = QTDockerEnv()
    qtde.prepare()

    # Run the Qlib backtest
    execute_log = qtde.check_output(
        local_path=str(template_path),
        entry="python generate.py",
        env={
            "FACTOR_SOURCE_INSTRUMENTS": FACTOR_COSTEER_SETTINGS.source_data_instruments,
            "QLIB_PROVIDER_URI": FactorBasePropSetting().provider_uri,
            "QLIB_REGION": FactorBasePropSetting().region,
        },
    )

    assert (Path(__file__).parent / "factor_data_template" / "daily_pv_all.h5").exists(), (
        "daily_pv_all.h5 is not generated. It means rdagent/scenarios/qlib/experiment/factor_data_template/generate.py is not executed correctly. Please check the log: \n"
        + execute_log
    )
    assert (Path(__file__).parent / "factor_data_template" / "daily_pv_debug.h5").exists(), (
        "daily_pv_debug.h5 is not generated. It means rdagent/scenarios/qlib/experiment/factor_data_template/generate.py is not executed correctly. Please check the log: \n"
        + execute_log
    )

    Path(FACTOR_COSTEER_SETTINGS.data_folder).mkdir(parents=True, exist_ok=True)
    shutil.copy(
        Path(__file__).parent / "factor_data_template" / "daily_pv_all.h5",
        Path(FACTOR_COSTEER_SETTINGS.data_folder) / "daily_pv.h5",
    )
    shutil.copy(
        Path(__file__).parent / "factor_data_template" / "README.md",
        Path(FACTOR_COSTEER_SETTINGS.data_folder) / "README.md",
    )

    Path(FACTOR_COSTEER_SETTINGS.data_folder_debug).mkdir(parents=True, exist_ok=True)
    shutil.copy(
        Path(__file__).parent / "factor_data_template" / "daily_pv_debug.h5",
        Path(FACTOR_COSTEER_SETTINGS.data_folder_debug) / "daily_pv.h5",
    )
    shutil.copy(
        Path(__file__).parent / "factor_data_template" / "README.md",
        Path(FACTOR_COSTEER_SETTINGS.data_folder_debug) / "README.md",
    )


def get_file_desc(p: Path, variable_list=[]) -> str:
    """
    Get the description of a file based on its type.

    Parameters
    ----------
    p : Path
        The path of the file.

    Returns
    -------
    str
        The description of the file.
    """
    p = Path(p)

    JJ_TPL = Environment(undefined=StrictUndefined).from_string("""
# {{file_name}}

## File Type
{{type_desc}}

## Content Overview
{{content}}
""")

    if p.name.endswith(".h5"):
        df = pd.read_hdf(p)
        pd.set_option("display.max_columns", None)
        pd.set_option("display.max_rows", None)
        pd.set_option("display.max_colwidth", None)

        df_info = "### Data Structure\n"
        df_info += (
            f"- Index: MultiIndex with levels {df.index.names}\n"
            if isinstance(df.index, pd.MultiIndex)
            else f"- Index: {df.index.name}\n"
        )

        file_profile = get_hdf_file_profile(p)
        file_profile.update(_dataframe_profile(df))
        df_info += "\n### Dataset Coverage and Quality\n"
        df_info += _profile_markdown(file_profile) + "\n"

        df_info += "\n### Columns\n"
        columns = df.dtypes.to_dict()
        grouped_columns = {}

        for col in columns:
            if col.startswith("$"):
                prefix = col.split("_")[0] if "_" in col else col
                grouped_columns.setdefault(prefix, []).append(col)
            else:
                grouped_columns.setdefault("other", []).append(col)

        if variable_list:
            df_info += "#### Relevant Columns:\n"
            relevant_line = ", ".join(f"{col}: {columns[col]}" for col in variable_list if col in columns)
            df_info += relevant_line + "\n"
        else:
            df_info += "#### All Columns:\n"
            for prefix, cols in sorted(grouped_columns.items()):
                header = "Other Columns" if prefix == "other" else f"{prefix} Related Columns"
                df_info += f"\n#### {header}:\n"
                line = ", ".join(f"{col}: {columns[col]}" for col in sorted(cols))
                df_info += line + "\n"

        if "REPORT_PERIOD" in df.columns:
            one_instrument = df.index.get_level_values("instrument")[0]
            df_on_one_instrument = df.loc[pd.IndexSlice[:, one_instrument], ["REPORT_PERIOD"]]
            df_info += "\n### Sample Data\n"
            df_info += f"Showing data for instrument {one_instrument}:\n"
            df_info += str(df_on_one_instrument.head(5))

        return JJ_TPL.render(
            file_name=p.name,
            type_desc="HDF5 Data File",
            content=df_info,
        )

    if p.name.endswith(".md"):
        with open(p) as f:
            content = f.read()
            return JJ_TPL.render(
                file_name=p.name,
                type_desc="Markdown Documentation",
                content=content,
            )

    else:
        raise NotImplementedError(
            f"file type {p.name} is not supported. Please implement its description function.",
        )


def get_data_folder_intro(fname_reg: str = ".*", flags=0, variable_mapping=None) -> str:
    """
    Directly get the info of the data folder.
    It is for preparing prompting message.

    Parameters
    ----------
    fname_reg : str
        a regular expression to filter the file name.

    flags: str
        flags for re.match

    Returns
    -------
        str
            The description of the data folder.
    """

    if (
        not Path(FACTOR_COSTEER_SETTINGS.data_folder).exists()
        or not Path(FACTOR_COSTEER_SETTINGS.data_folder_debug).exists()
    ):
        # FIXME: (xiao) I think this is writing in a hard-coded way.
        # get data folder intro does not imply that we are generating the data folder.
        generate_data_folder_from_qlib()
    full_data_path = Path(FACTOR_COSTEER_SETTINGS.data_folder) / "daily_pv.h5"
    content_l = []
    if full_data_path.exists():
        content_l.append(
            "# Full Execution Dataset\n\n"
            "The summary below describes the full dataset used to execute factor code and evaluate experiments.\n\n"
            + _profile_markdown(get_hdf_file_profile(full_data_path)),
        )
    content_l.append(
        "# Coding and Debug Dataset\n\n"
        "The following files are a smaller sample used while implementing and debugging factors. "
        "Its coverage is not the evaluation period.",
    )
    for p in sorted(Path(FACTOR_COSTEER_SETTINGS.data_folder_debug).iterdir()):
        if re.match(fname_reg, p.name, flags) is not None:
            if variable_mapping:
                content_l.append(get_file_desc(p, variable_mapping.get(p.stem, [])))
            else:
                content_l.append(get_file_desc(p))
    return "\n----------------- file splitter -------------\n".join(content_l)
