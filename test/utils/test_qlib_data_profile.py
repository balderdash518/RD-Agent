from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from rdagent.scenarios.qlib.experiment.utils import get_file_desc, get_hdf_file_profile

_EXPECTED_ROWS = 4
_EXPECTED_INSTRUMENTS = 2
_SHA256_LENGTH = 64


@pytest.mark.offline
def test_hdf_profile_and_prompt_are_stable_and_show_coverage(tmp_path: Path) -> None:
    index = pd.MultiIndex.from_product(
        [pd.to_datetime(["2024-01-02", "2024-01-03"]), ["SH600000", "SZ000001"]],
        names=["datetime", "instrument"],
    )
    frame = pd.DataFrame(
        {
            "$close": [10.0, 11.0, np.nan, 12.0],
            "$volume": [100.0, 200.0, 300.0, 400.0],
        },
        index=index,
    )
    path = tmp_path / "daily_pv.h5"
    frame.to_hdf(path, key="data")

    profile = get_hdf_file_profile(path)
    first_description = get_file_desc(path)
    second_description = get_file_desc(path)

    assert profile["rows"] == _EXPECTED_ROWS
    assert profile["instruments"] == _EXPECTED_INSTRUMENTS
    assert profile["date_start"].startswith("2024-01-02")
    assert profile["date_end"].startswith("2024-01-03")
    assert len(profile["sha256"]) == _SHA256_LENGTH
    assert first_description == second_description
    assert "Dataset Coverage and Quality" in first_description
    assert "Rows: 4" in first_description
    assert "$close=25.0000%" in first_description
