"""Tests for the DrivingScenario domain model."""

import pytest
from pydantic import ValidationError

from autoforge.domain.scenario import DrivingScenario


def _flat(
    duration_s: float = 10.0, speed_mps: float = 20.0, timestep_s: float = 1.0, name: str = "flat"
) -> DrivingScenario:
    n = int(duration_s / timestep_s) + 1
    time = [float(i) * timestep_s for i in range(n)]
    speed = [speed_mps] * n
    return DrivingScenario(name=name, time_s=time, speed_mps=speed, grade_fraction=[0.0] * n)


class TestScenario:
    def test_valid_flat_scenario(self) -> None:
        scenario = _flat()
        assert scenario.sample_count == 11
        assert scenario.timestep_s == pytest.approx(1.0)
        assert scenario.duration_s == pytest.approx(10.0)

    def test_distance_trapezoid_integration(self) -> None:
        scenario = _flat(duration_s=60.0, speed_mps=30.0)
        assert scenario.distance_km == pytest.approx(1.8)

    def test_distance_for_accelerating_profile(self) -> None:
        # 0 -> 10 m/s over 10 s with 1 s steps: 5 m/s average each second.
        time = [float(i) for i in range(11)]
        speed = [float(i) for i in range(11)]
        scenario = DrivingScenario(
            name="ramp", time_s=time, speed_mps=speed, grade_fraction=[0.0] * 11
        )
        assert scenario.distance_km == pytest.approx(0.05)

    def test_two_samples_is_minimum(self) -> None:
        DrivingScenario(time_s=[0.0, 1.0], speed_mps=[0.0, 0.0], grade_fraction=[0.0, 0.0])

    def test_less_than_two_samples_rejected(self) -> None:
        with pytest.raises(ValidationError):
            DrivingScenario(time_s=[0.0], speed_mps=[0.0], grade_fraction=[0.0])

    def test_empty_rejected(self) -> None:
        with pytest.raises(ValidationError):
            DrivingScenario(time_s=[], speed_mps=[], grade_fraction=[])

    def test_mismatched_lengths_rejected(self) -> None:
        with pytest.raises(ValidationError):
            DrivingScenario(time_s=[0.0, 1.0], speed_mps=[0.0], grade_fraction=[0.0, 0.0])

    def test_time_must_start_at_zero(self) -> None:
        with pytest.raises(ValidationError):
            DrivingScenario(time_s=[1.0, 2.0], speed_mps=[0.0, 0.0], grade_fraction=[0.0, 0.0])

    def test_negative_speed_rejected(self) -> None:
        with pytest.raises(ValidationError):
            DrivingScenario(time_s=[0.0, 1.0], speed_mps=[0.0, -1.0], grade_fraction=[0.0, 0.0])

    def test_implausible_speed_rejected_as_unit_error(self) -> None:
        # 130 km/h fed in as m/s exceeds the sanity bound and must be rejected.
        with pytest.raises(ValidationError):
            DrivingScenario(time_s=[0.0, 1.0], speed_mps=[130.0, 130.0], grade_fraction=[0.0, 0.0])

    def test_grade_out_of_range_rejected(self) -> None:
        with pytest.raises(ValidationError):
            DrivingScenario(time_s=[0.0, 1.0], speed_mps=[1.0, 1.0], grade_fraction=[0.0, 1.5])

    def test_zero_timestep_rejected(self) -> None:
        with pytest.raises(ValidationError):
            DrivingScenario(time_s=[0.0, 0.0], speed_mps=[1.0, 1.0], grade_fraction=[0.0, 0.0])

    def test_negative_timestep_rejected(self) -> None:
        with pytest.raises(ValidationError):
            DrivingScenario(time_s=[0.0, -1.0], speed_mps=[1.0, 1.0], grade_fraction=[0.0, 0.0])

    def test_non_uniform_timestep_rejected(self) -> None:
        with pytest.raises(ValidationError):
            DrivingScenario(
                time_s=[0.0, 1.0, 3.0], speed_mps=[1.0, 1.0, 1.0], grade_fraction=[0.0, 0.0, 0.0]
            )

    def test_nan_values_rejected(self) -> None:
        with pytest.raises(ValidationError):
            DrivingScenario(
                time_s=[0.0, float("nan")], speed_mps=[1.0, 1.0], grade_fraction=[0.0, 0.0]
            )

    def test_tiny_timestep_accepted(self) -> None:
        scenario = DrivingScenario(
            time_s=[0.0, 0.001, 0.002], speed_mps=[5.0, 5.0, 5.0], grade_fraction=[0.0, 0.0, 0.0]
        )
        assert scenario.timestep_s == pytest.approx(0.001)

    def test_immutable(self) -> None:
        scenario = _flat()
        with pytest.raises(ValidationError):
            scenario.name = "changed"  # type: ignore[misc]

    def test_serialization_round_trip(self) -> None:
        scenario = _flat(name="roundtrip")
        restored = DrivingScenario.model_validate_json(scenario.model_dump_json())
        assert restored == scenario
