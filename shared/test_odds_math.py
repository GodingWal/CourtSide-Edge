import pytest

from shared.odds_math import american_to_decimal, decimal_to_american, implied_probability


def test_american_to_decimal():
    assert american_to_decimal(100) == 2.0
    assert american_to_decimal(150) == 2.5
    assert american_to_decimal(-110) == pytest.approx(1.9091, abs=1e-4)
    assert american_to_decimal(-200) == 1.5


def test_decimal_to_american_round_trip():
    # -100 is excluded: it equals +100 (decimal 2.0), which converts back as +100.
    for odds in (-250, -110, 100, 120, 300):
        assert decimal_to_american(american_to_decimal(odds)) == odds


def test_implied_probability():
    assert implied_probability(100) == 0.5
    assert implied_probability(-110) == pytest.approx(0.5238, abs=1e-4)
    assert implied_probability(200) == pytest.approx(1 / 3, abs=1e-6)


def test_invalid_american_odds_raise():
    for bad in (0, 50, -99):
        with pytest.raises(ValueError):
            american_to_decimal(bad)
        with pytest.raises(ValueError):
            implied_probability(bad)
    with pytest.raises(ValueError):
        decimal_to_american(1.0)
