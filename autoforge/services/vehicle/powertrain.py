"""SIMPLIFIED LONGITUDINAL EV ENERGY MODEL.

This is a longitudinal, power/energy model, not complete vehicle dynamics. It
prescribes the trajectory (a DrivingScenario) and asks "what battery energy
does following it cost?". Speed is never re-derived from torque; if the battery
cannot supply the requested power, the vehicle simply fails to meet it
(power_limited / depleted) and the prescribed profile continues. A real
controller would trade off speed, which is out of scope here.

Longitudinal force at the wheels:

    F = m*a + 0.5*rho*Cd*A*v^2 + Crr*m*g*cos(theta) + m*g*sin(theta)

    inertial     m*a
    aero drag    0.5*rho*Cd*A*v^2
    rolling      Crr*m*g*cos(theta)
    grade        m*g*sin(theta)

where theta = atan(grade). Wheel power is F*v; positive drives, negative
brakes. Driving draws

    battery_draw = wheel_power / (motor_eff * drivetrain_eff) + aux_power

Braking recovers at most

    recovered = min(-wheel_power * regen_eff, max_regen_power,
                    max_charge_c_rate * nominal_energy, SOC headroom / dt)

Energy is never created: regen that cannot be stored is discarded and counted,
never credited. SOC is a simple energy integral over usable energy:

    dSOC = -(draw - recovered) * dt / usable_energy_kwh

Assumptions (all in PowertrainConfig, versioned):
- constant motor/drivetrain/regen efficiencies; no efficiency maps
- constant auxiliary load; no thermal/HVAC coupling
- battery voltage and C-rate limits modelled as flat power caps
- grade constant within each scenario interval; interval uses average speed
- no tyre slip, suspension, drivetrain inertia, or battery internal losses
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field, model_validator

from autoforge.domain.battery import BatteryPack
from autoforge.domain.motor import Motor
from autoforge.domain.scenario import DrivingScenario
from autoforge.domain.vehicle import VehicleVariant
from autoforge.services.vehicle.result import ResultSummary, SimulationResult, TrajectoryPoint
from autoforge.simulation.engine import SimulationContext, SimulationEngine, SimulationRun

SECONDS_PER_DAY = 86400.0


class PowertrainConfig(BaseModel):
    """Versionable engineering parameters of the energy model.

    Every value has a documented physical meaning so a run records the exact
    assumptions that produced it.
    """

    model_config = ConfigDict(frozen=True)

    gravity_mps2: float = Field(default=9.81, gt=0)
    air_density_kgm3: float = Field(default=1.225, gt=0, description="Sea-level air density")
    rolling_resistance_coefficient: float = Field(default=0.011, ge=0)
    motor_efficiency: float = Field(default=0.92, gt=0, lt=1, description="Drive efficiency")
    drivetrain_efficiency: float = Field(default=0.95, gt=0, lt=1)
    regen_efficiency: float = Field(
        default=0.65,
        gt=0,
        lt=1,
        description="Fraction of negative wheel power returned to the battery",
    )
    auxiliary_power_kw: float = Field(
        default=0.6, ge=0, description="Constant HVAC/electronics/infotainment load"
    )
    max_regen_power_kw: float = Field(default=80.0, gt=0, description="Regen power cap")
    soc_min: float = Field(default=0.0, ge=0, lt=1)
    soc_max: float = Field(default=1.0, gt=0, le=1)
    cargo_mass_kg: float = Field(default=0.0, ge=0, description="Added to kerb mass")

    @model_validator(mode="after")
    def _soc_bounds(self) -> PowertrainConfig:
        if not self.soc_min < self.soc_max:
            raise ValueError("soc_min must be below soc_max")
        return self


@dataclass(frozen=True)
class IntervalResult:
    """Outcome of one timestep of the energy model."""

    wheel_power_kw: float
    traction_power_kw: float
    battery_power_kw: float
    recovered_power_kw: float
    aux_power_kw: float
    energy_consumed_kwh: float
    energy_recovered_kwh: float
    energy_discarded_kwh: float
    power_limited: bool
    depleted: bool
    soc: float


def _grade_components(grade_fraction: float) -> tuple[float, float]:
    """Return (sin, cos) of the grade angle from its slope fraction."""
    denom = math.sqrt(1.0 + grade_fraction * grade_fraction)
    return grade_fraction / denom, 1.0 / denom


def longitudinal_force(
    config: PowertrainConfig,
    *,
    total_mass_kg: float,
    frontal_area_m2: float,
    drag_coefficient: float,
    speed_mps: float,
    acceleration_mps2: float,
    grade_fraction: float,
) -> float:
    """Net longitudinal force (N) the wheels must deliver to follow the profile.

    Positive means propulsion is required; negative means the road and inertia
    are braking the vehicle (regen opportunity).
    """
    sin_t, cos_t = _grade_components(grade_fraction)
    drag = 0.5 * config.air_density_kgm3 * drag_coefficient * frontal_area_m2 * speed_mps**2
    rolling = config.rolling_resistance_coefficient * total_mass_kg * config.gravity_mps2 * cos_t
    grade = total_mass_kg * config.gravity_mps2 * sin_t
    inertial = total_mass_kg * acceleration_mps2
    return drag + rolling + grade + inertial


def step_interval(
    config: PowertrainConfig,
    pack: BatteryPack,
    motor: Motor,
    *,
    soc: float,
    wheel_power_kw: float,
    dt_s: float,
) -> IntervalResult:
    """Advance the battery model one timestep for a given wheel power.

    Discharge is capped by motor peak power and the battery C-rate; regen is
    capped by ``max_regen_power_kw``, the charge C-rate, and SOC headroom.
    The battery can never go below ``soc_min`` or above ``soc_max``.
    """
    if dt_s <= 0.0:
        raise ValueError(f"timestep must be positive, got {dt_s!r}")
    if not config.soc_min <= soc <= config.soc_max:
        raise ValueError(f"soc {soc!r} outside configured bounds")

    dt_h = dt_s / 3600.0
    usable_kwh = pack.usable_energy_kwh
    aux = config.auxiliary_power_kw

    if wheel_power_kw >= 0.0:
        demand = wheel_power_kw / (config.motor_efficiency * config.drivetrain_efficiency)
        traction = min(demand, motor.peak_power_kw)
        power_limited = traction < demand - 1e-9

        max_discharge = pack.max_discharge_c_rate * pack.nominal_energy_kwh
        battery_draw = traction + aux
        if battery_draw > max_discharge + 1e-9:
            battery_draw = max_discharge
            power_limited = True
            traction = max(0.0, battery_draw - aux)

        requested_kwh = battery_draw * dt_h
        available_kwh = (soc - config.soc_min) * usable_kwh
        depleted = requested_kwh > available_kwh + 1e-12
        if depleted:
            battery_draw = max(0.0, available_kwh) / dt_h
            traction = max(0.0, battery_draw - aux)
            power_limited = True
            soc = config.soc_min
        else:
            soc = soc - requested_kwh / usable_kwh

        return IntervalResult(
            wheel_power_kw=wheel_power_kw,
            traction_power_kw=traction,
            battery_power_kw=battery_draw,
            recovered_power_kw=0.0,
            aux_power_kw=aux,
            energy_consumed_kwh=battery_draw * dt_h,
            energy_recovered_kwh=0.0,
            energy_discarded_kwh=0.0,
            power_limited=power_limited,
            depleted=depleted,
            soc=soc,
        )

    # Braking: recover what the battery can accept, discard the rest.
    available_regen = -wheel_power_kw * config.regen_efficiency
    max_charge = pack.max_charge_c_rate * pack.nominal_energy_kwh
    # Headroom is on SOC; aux drains concurrently, so it adds to headroom.
    headroom_kwh = (config.soc_max - soc) * usable_kwh + aux * dt_h
    recovered_kwh = max(
        0.0,
        min(
            available_regen * dt_h,
            config.max_regen_power_kw * dt_h,
            max_charge * dt_h,
            headroom_kwh,
        ),
    )
    recovered_kw = recovered_kwh / dt_h
    discarded_kwh = max(0.0, available_regen * dt_h - recovered_kwh)
    soc = min(config.soc_max, soc + (recovered_kwh - aux * dt_h) / usable_kwh)
    soc = max(soc, config.soc_min)  # empty battery cannot keep aux running (corner case)

    return IntervalResult(
        wheel_power_kw=wheel_power_kw,
        traction_power_kw=0.0,
        battery_power_kw=aux - recovered_kw,
        recovered_power_kw=recovered_kw,
        aux_power_kw=aux,
        energy_consumed_kwh=aux * dt_h,
        energy_recovered_kwh=recovered_kwh,
        energy_discarded_kwh=discarded_kwh,
        power_limited=False,
        depleted=False,
        soc=soc,
    )


class PowertrainSubsystem:
    """Engine subsystem that replays a scenario one interval per engine step.

    The engine's ``step_days`` is set to the scenario timestep expressed in
    days (done by ``PowertrainSimulator``), so each engine tick consumes one
    scenario interval. Once the scenario is exhausted, further steps are
    no-ops, which also absorbs any spurious partial final step from the engine.
    """

    name = "powertrain"

    def __init__(
        self,
        variant: VehicleVariant,
        scenario: DrivingScenario,
        config: PowertrainConfig,
        initial_soc: float = 1.0,
    ) -> None:
        if not config.soc_min <= initial_soc <= config.soc_max:
            raise ValueError(f"initial_soc {initial_soc!r} outside configured bounds")
        self._variant = variant
        self._scenario = scenario
        self._config = config
        self._soc = initial_soc
        self._index = 0
        self._points: list[TrajectoryPoint] = []
        self._distance_km = 0.0
        self._consumed_kwh = 0.0
        self._recovered_kwh = 0.0
        self._discarded_kwh = 0.0
        self._peak_power_kw = 0.0
        self._peak_regen_kw = 0.0
        self._min_soc = initial_soc
        self._power_limited_s = 0.0
        self._depleted_emitted = False

    @property
    def index(self) -> int:
        """Number of scenario intervals consumed so far."""
        return self._index

    def step(self, ctx: SimulationContext, dt_days: float) -> None:
        del dt_days  # scenario timestep is the authoritative time base
        if self._index >= self._scenario.sample_count - 1:
            return

        i = self._index
        dt_s = self._scenario.timestep_s
        v0 = self._scenario.speed_mps[i]
        v1 = self._scenario.speed_mps[i + 1]
        grade = self._scenario.grade_fraction[i]
        v_avg = 0.5 * (v0 + v1)
        acceleration = (v1 - v0) / dt_s
        total_mass = self._variant.kerb_mass_kg + self._config.cargo_mass_kg

        force = longitudinal_force(
            self._config,
            total_mass_kg=total_mass,
            frontal_area_m2=self._variant.frontal_area_m2,
            drag_coefficient=self._variant.drag_coefficient,
            speed_mps=v_avg,
            acceleration_mps2=acceleration,
            grade_fraction=grade,
        )
        wheel_power = force * v_avg / 1000.0
        interval = step_interval(
            self._config,
            self._variant.battery_pack,
            self._variant.motor,
            soc=self._soc,
            wheel_power_kw=wheel_power,
            dt_s=dt_s,
        )

        self._soc = interval.soc
        self._distance_km += v_avg * dt_s / 1000.0
        self._consumed_kwh += interval.energy_consumed_kwh
        self._recovered_kwh += interval.energy_recovered_kwh
        self._discarded_kwh += interval.energy_discarded_kwh
        self._peak_power_kw = max(self._peak_power_kw, interval.battery_power_kw)
        self._peak_regen_kw = max(self._peak_regen_kw, interval.recovered_power_kw)
        self._min_soc = min(self._min_soc, interval.soc)
        if interval.power_limited or interval.depleted:
            self._power_limited_s += dt_s

        if interval.depleted and not self._depleted_emitted:
            self._depleted_emitted = True
            ctx.emit(
                self.name,
                "battery_depleted",
                time_s=self._scenario.time_s[i + 1],
                soc=interval.soc,
            )

        self._points.append(
            TrajectoryPoint(
                time_s=self._scenario.time_s[i + 1],
                speed_mps=v1,
                acceleration_mps2=acceleration,
                grade_fraction=grade,
                wheel_power_kw=wheel_power,
                traction_power_kw=interval.traction_power_kw,
                battery_power_kw=interval.battery_power_kw,
                recovered_power_kw=interval.recovered_power_kw,
                aux_power_kw=interval.aux_power_kw,
                soc=interval.soc,
                energy_consumed_kwh=self._consumed_kwh,
                energy_recovered_kwh=self._recovered_kwh,
                distance_km=self._distance_km,
                power_limited=interval.power_limited,
                depleted=interval.depleted,
            )
        )
        self._index += 1

    def build_result(self) -> SimulationResult:
        """Assemble the typed result from accumulated state."""
        net_energy = self._consumed_kwh - self._recovered_kwh
        average_consumption: float | None = None
        estimated_range: float | None = None
        if self._distance_km > 1e-9:
            average_consumption = net_energy / self._distance_km
            if average_consumption > 1e-9:
                estimated_range = self._variant.battery_pack.usable_energy_kwh / average_consumption
        summary = ResultSummary(
            duration_s=self._scenario.duration_s,
            distance_km=self._distance_km,
            energy_consumed_kwh=self._consumed_kwh,
            energy_recovered_kwh=self._recovered_kwh,
            regen_discarded_kwh=self._discarded_kwh,
            net_energy_kwh=net_energy,
            average_consumption_kwh_per_km=average_consumption,
            peak_power_kw=self._peak_power_kw,
            peak_regen_power_kw=self._peak_regen_kw,
            min_soc=self._min_soc,
            final_soc=self._soc,
            estimated_range_km=estimated_range,
            power_limited_seconds=self._power_limited_s,
        )
        return SimulationResult(summary=summary, trajectory=tuple(self._points))


@dataclass(frozen=True)
class PowertrainSimulation:
    """A completed powertrain run: the engine record, typed result, and events."""

    run: SimulationRun
    result: SimulationResult
    events: tuple[dict[str, object], ...] = ()


class PowertrainSimulator:
    """Runs a vehicle variant through a scenario on the shared engine.

    Deterministic by construction: no randomness is drawn, so identical
    vehicle, scenario, configuration, and software version reproduce identical
    results. The seed is recorded on the run for identity but has no effect on
    the physics.
    """

    def __init__(
        self,
        *,
        variant: VehicleVariant,
        scenario: DrivingScenario,
        config: PowertrainConfig | None = None,
        seed: int = 0,
    ) -> None:
        self._variant = variant
        self._scenario = scenario
        self._config = config if config is not None else PowertrainConfig()
        self._seed = seed

    def simulate(self, *, initial_soc: float = 1.0) -> PowertrainSimulation:
        """Replay the scenario and return the run record and typed result."""
        if not self._config.soc_min <= initial_soc <= self._config.soc_max:
            raise ValueError(f"initial_soc {initial_soc!r} outside configured bounds")

        engine = SimulationEngine(
            seed=self._seed,
            step_days=self._scenario.timestep_s / SECONDS_PER_DAY,
            config={
                "system": "powertrain",
                "vehicle": f"{self._variant.model.name} {self._variant.trim_name}",
                "scenario": {
                    "name": self._scenario.name,
                    "duration_s": self._scenario.duration_s,
                    "timestep_s": self._scenario.timestep_s,
                },
                "powertrain_config": self._config.model_dump(),
                "initial_soc": initial_soc,
            },
        )
        subsystem = PowertrainSubsystem(
            self._variant, self._scenario, self._config, initial_soc=initial_soc
        )
        engine.add_subsystem(subsystem)
        run = engine.run(days=self._scenario.duration_s / SECONDS_PER_DAY)
        result = subsystem.build_result()
        run.result = result.summary.model_dump()
        return PowertrainSimulation(run=run, result=result, events=tuple(engine.log.entries))
