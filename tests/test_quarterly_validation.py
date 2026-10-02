import importlib.util
from pathlib import Path
import numpy as np
import pandas as pd

spec = importlib.util.spec_from_file_location("quarterly", Path(__file__).resolve().parents[1] / "code/quarterly_validation.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_missing_month_cannot_be_used_as_previous_month():
    frame = pd.DataFrame({"fed_funds_effective": [1., 2., 4., 5.], "core_pce_yoy": [2., 2., 3., 3.]},
                         index=pd.to_datetime(["2024-01-31", "2024-02-29", "2024-04-30", "2024-05-31"]))
    result = module.design(frame, "US")
    assert pd.Timestamp("2024-04-30") not in result.index
    assert result.loc["2024-05-31", "lag_rate"] == 4


def test_sample_estimate_recovers_known_coefficients():
    rng = np.random.default_rng(8)
    inflation = rng.normal(2, 1, 180)
    rates = [1.]
    for pi in inflation[1:]:
        rates.append(.2 + .3 * pi + .6 * rates[-1])
    frame = pd.DataFrame({"rate": rates, "inflation": inflation}, index=pd.date_range("2000-01-31", periods=180, freq="ME"))
    frame["lag_rate"] = frame.rate.shift(1)
    fit = module.estimate(frame.dropna())
    assert abs(fit["beta_inflation"] - .3) < 1e-8
    assert abs(fit["rho"] - .6) < 1e-8
    assert abs(fit["phi_long_run"] - .75) < 1e-8
