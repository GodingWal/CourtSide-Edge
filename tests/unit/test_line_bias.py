"""Line-band bias: what is measured, what is shrunk, and what is left alone.

The danger this module guards against is a thin band inventing a correction. A star-band
bias is worth having only because thousands of settled forecasts show it; the same arithmetic
applied to eleven games would be curve-fitting noise, so the tests spend most of their weight
on the shrinkage and the sample gates.
"""

from __future__ import annotations

import pytest
from wnba_services.forecasting.line_bias import (
    IDENTITY_BIAS,
    SHRINK_K,
    LineBandBias,
    fit_line_bias,
)


def _points(line: float, error: float, count: int) -> list[tuple[float, float, float]]:
    projected = 12.0
    return [(line, projected, projected + error) for _ in range(count)]


def test_thin_bands_earn_nothing() -> None:
    fitted = fit_line_bias(_points(17.0, 2.0, 29), market="points")
    assert fitted.bands == ()
    assert not fitted.is_fitted
    assert fitted.bias_for(17.0) == 0.0


def test_a_measured_band_is_shrunk_toward_zero() -> None:
    count = 100
    fitted = fit_line_bias(_points(17.0, 2.0, count), market="points")
    (band,) = fitted.bands
    expected = 2.0 * (count / (count + SHRINK_K))
    assert band.bias == pytest.approx(expected)
    assert fitted.is_fitted
    assert fitted.bias_for(17.0) == pytest.approx(expected)


def test_bias_is_applied_only_inside_its_band() -> None:
    fitted = fit_line_bias(_points(17.0, 2.0, 100), market="points")
    assert fitted.bias_for(16.9) > 0.0
    # 17 sits in the 15-20 band; 21 falls outside every fitted band and gets no correction.
    assert fitted.bias_for(21.0) == 0.0
    assert fitted.apply(18.0, 17.0) == pytest.approx(18.0 + fitted.bias_for(17.0))


def test_apply_never_takes_a_mean_below_zero() -> None:
    bias = LineBandBias.from_payload(
        {"bands": [{"lo": 0.0, "hi": 10.0, "bias": -3.0, "sample_size": 50}], "is_fitted": True}
    )
    assert bias.apply(1.0, 5.0) == 0.0


def test_payload_round_trip() -> None:
    fitted = fit_line_bias(_points(17.0, 2.0, 100) + _points(6.0, -0.5, 60), market="points")
    restored = LineBandBias.from_payload(fitted.to_payload())
    assert restored == fitted


def test_payload_survives_json() -> None:
    # The top band is open-ended (hi=inf), and JSONB rejects Infinity: the payload must round
    # trip through the strict JSON the database enforces, not Python's permissive default.
    import json
    import math

    fitted = fit_line_bias(_points(27.0, 3.0, 100), market="points")
    assert any(math.isinf(band.hi) for band in fitted.bands)
    payload = json.loads(json.dumps(fitted.to_payload(), allow_nan=False))
    restored = LineBandBias.from_payload(payload)
    assert restored == fitted


def test_identity_bias_is_a_no_op() -> None:
    assert IDENTITY_BIAS.bias_for(17.0) == 0.0
    assert IDENTITY_BIAS.apply(18.0, 17.0) == 18.0


def test_mixed_bands_fit_independently() -> None:
    fitted = fit_line_bias(_points(6.0, -1.0, 50) + _points(17.0, 2.0, 50), market="points")
    assert fitted.bias_for(6.0) < 0.0 < fitted.bias_for(17.0)
