"""Unit tests for measured line-band projection bias."""

from __future__ import annotations

from services.wnba_services.forecasting.line_bias import (
    IDENTITY_BIAS,
    LineBand,
    LineBandBias,
    fit_line_bias,
)


def _points(line: float, error: float, count: int) -> list[tuple[float, float, float]]:
    # (line, projected, actual) with actual - projected == error
    return [(line, 10.0, 10.0 + error) for _ in range(count)]


def test_thin_band_earns_no_bias() -> None:
    points = _points(17.5, 2.0, 29)
    fitted = fit_line_bias(points, market="points")
    assert not fitted.is_fitted
    assert fitted.bias_for(17.5) == 0.0


def test_bias_is_shrunk_toward_zero() -> None:
    points = _points(17.5, 2.0, 75)
    fitted = fit_line_bias(points, market="points")
    assert fitted.is_fitted
    # 75 samples, raw bias 2.0, shrink = 75 / (75 + 25) = 0.75
    assert abs(fitted.bias_for(17.5) - 1.5) < 1e-9


def test_bias_applies_only_within_band() -> None:
    points = _points(17.5, 2.0, 100)
    fitted = fit_line_bias(points, market="points")
    assert fitted.bias_for(17.5) > 0.0
    assert fitted.bias_for(9.5) == 0.0
    assert fitted.bias_for(26.5) == 0.0


def test_apply_never_below_zero() -> None:
    bias = LineBandBias(
        market="points",
        bands=(LineBand(lower=15.0, upper=20.0, bias=-3.0, sample_size=50),),
        is_fitted=True,
    )
    assert bias.apply(1.0, 17.5) == 0.0
    assert bias.apply(10.0, 17.5) == 7.0


def test_payload_round_trip() -> None:
    bias = LineBandBias(
        market="points",
        bands=(
            LineBand(lower=0.0, upper=10.0, bias=0.0, sample_size=12),
            LineBand(lower=10.0, upper=15.0, bias=-0.4, sample_size=64),
            LineBand(lower=15.0, upper=20.0, bias=1.8, sample_size=81),
            LineBand(lower=20.0, upper=None, bias=0.0, sample_size=9),
        ),
        is_fitted=True,
    )
    restored = LineBandBias.from_payload(bias.to_payload())
    assert restored == bias


def test_identity_bias_is_noop() -> None:
    assert IDENTITY_BIAS.bias_for(17.5) == 0.0
    assert IDENTITY_BIAS.apply(12.34, 17.5) == 12.34


def test_mixed_bands_fit_independently() -> None:
    points = _points(7.5, -1.0, 40) + _points(22.5, 3.0, 40)
    fitted = fit_line_bias(points, market="points")
    assert fitted.is_fitted
    low = fitted.bias_for(7.5)
    high = fitted.bias_for(22.5)
    assert low < 0.0 < high
    # 40 samples, shrink = 40 / 65
    assert abs(low - (-1.0 * 40 / 65)) < 1e-9
    assert abs(high - (3.0 * 40 / 65)) < 1e-9
