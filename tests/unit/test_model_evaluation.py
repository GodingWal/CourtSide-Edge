import pytest
from wnba_services.learning_loop.evaluation import classify_error, expected_calibration_error


def test_expected_calibration_error_is_weighted_by_bucket_size() -> None:
    rows = [(0.6, True)] * 8 + [(0.9, False)] * 2
    assert expected_calibration_error(rows) == pytest.approx(0.5)


def test_empty_calibration_has_no_score() -> None:
    assert expected_calibration_error([]) is None


@pytest.mark.parametrize(
    ("values", "category", "avoidable"),
    [
        ((30.0, 22.0, 15.0, 8.0, None), "minutes_projection", True),
        ((30.0, 30.0, 15.0, 14.0, -1.5), "market_movement", True),
        ((30.0, 30.0, 15.0, 24.0, None), "rate_or_efficiency", True),
        ((30.0, 30.0, 15.0, 16.0, None), "random_variance", False),
    ],
)
def test_error_classification_has_stable_priority(
    values: tuple[float, float, float, float, float | None],
    category: str,
    avoidable: bool,
) -> None:
    result = classify_error(
        projected_minutes=values[0],
        actual_minutes=values[1],
        projected_stat=values[2],
        actual_stat=values[3],
        line_value=values[4],
    )
    assert result[0] == category
    assert result[1] is avoidable
