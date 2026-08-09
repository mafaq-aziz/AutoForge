"""Scenario builders and a small documented reference cycle.

Reference cycles are synthetic and deliberately simple so expected outputs can
be derived by hand from the equations in docs/powertrain.md. They are not real
driving cycles and make no claim to represent real-world driving.
"""

from __future__ import annotations

from autoforge.domain.scenario import DrivingScenario


def constant_speed_scenario(
    *,
    duration_s: float,
    speed_mps: float,
    timestep_s: float = 1.0,
    grade_fraction: float = 0.0,
    name: str = "constant_speed",
) -> DrivingScenario:
    """A flat or graded constant-speed profile.

    The profile has ``duration_s / timestep_s + 1`` samples covering 0..duration.
    """
    n = int(round(duration_s / timestep_s)) + 1
    time_s = [float(i) * timestep_s for i in range(n)]
    speed_profile = [speed_mps] * n
    grades = [grade_fraction] * n
    return DrivingScenario(name=name, time_s=time_s, speed_mps=speed_profile, grade_fraction=grades)


def reference_highway_cycle() -> DrivingScenario:
    """Reference scenario: 10 minutes at 30 m/s (108 km/h) on flat road.

    Hand-derived expected values (see docs/powertrain.md "Reference scenario"):
    with the demo vehicle and default PowertrainConfig, net consumption is
    about 0.163 kWh/km and range about 459 km.
    """
    return constant_speed_scenario(
        duration_s=600.0, speed_mps=30.0, timestep_s=1.0, name="reference_highway"
    )
