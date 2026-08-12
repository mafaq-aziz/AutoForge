"""SIMPLIFIED BATTERY ELECTRICAL, THERMAL, AND DEGRADATION MODEL.

This is a lumped, deterministic battery model for education/research. It is
deliberately not an electrochemical cell model.

Electrical:
    OCV(soc) = V_min + soc * (V_max - V_min)          # linear OCV simplification
    R*I^2 - OCV*I + P = 0                             # V = OCV - I*R, P = V*I
    I = (OCV - sqrt(OCV^2 - 4*R*P)) / (2*R)           # physical branch (sign follows P)
    V = OCV - I*R, and V*I == P exactly on that branch.

If OCV^2 < 4*R*P the pack cannot deliver the requested power at this voltage;
the model clamps to the maximum power point I = OCV/(2R) and flags
``power_limited`` instead of silently accepting an impossible value.

Thermal (lumped, Newton cooling):
    dT/dt = (I^2*R - k*(T - T_amb)) / C_th

Degradation (SIMPLIFIED DEGRADATION MODEL):
    throughput_kwh += |P| * dt
    EFC = throughput / usable_energy
    SOH = clamp(1 - 0.2*EFC / cycle_life_to_80_soh, soh_floor, 1)

Real battery aging depends on chemistry, cell design, temperature history,
calendar aging, manufacturing variation, and more; none of that is modeled
here, so SOH is a transparent energy-throughput baseline only.

SOC is integrated from net power (positive discharges), clamped to the bounds
given at each step. When driven by a powertrain result, the powertrain remains
authoritative; the model's own estimate is used as a consistency check.

Faults are deterministic rules (see detect_faults). No ML.
"""

from __future__ import annotations

import math

from autoforge.domain.battery import BatteryPack
from autoforge.domain.battery_state import BatteryFaultCode, BatteryState
from autoforge.domain.vehicle import VehicleVariant
from autoforge.services.battery.config import BatteryConfig
from autoforge.services.battery.result import (
    BatterySimulationResult,
    BatteryStatePoint,
    BatterySummary,
)
from autoforge.services.vehicle.powertrain import PowertrainSimulation

SECONDS_PER_HOUR = 3600.0


def ocv_voltage(pack: BatteryPack, soc: float) -> float:
    """Linear open-circuit voltage approximation between the pack's voltage bounds."""
    return pack.min_voltage_v + soc * (pack.max_voltage_v - pack.min_voltage_v)


def current_from_power(ocv_v: float, resistance_ohm: float, power_w: float) -> tuple[float, bool]:
    """Pack current for a requested power; returns (current_a, power_limited).

    Positive power discharges (positive current); negative power charges.
    When the requested power exceeds the pack's capability at this OCV, the
    current is clamped to the maximum-power point and ``power_limited`` is set.
    """
    if resistance_ohm <= 0:
        raise ValueError(f"resistance must be positive, got {resistance_ohm!r}")
    discriminant = ocv_v * ocv_v - 4.0 * resistance_ohm * power_w
    if discriminant < 0.0:
        return ocv_v / (2.0 * resistance_ohm), True
    return (ocv_v - math.sqrt(discriminant)) / (2.0 * resistance_ohm), False


def thermal_step(config: BatteryConfig, temperature_k: float, heat_w: float, dt_s: float) -> float:
    """One lumped-thermal step: resistive heat minus Newton cooling to ambient."""
    cooling_w = config.cooling_coefficient_w_per_k * (temperature_k - config.ambient_temperature_k)
    return temperature_k + (heat_w - cooling_w) * dt_s / config.thermal_capacity_j_per_k


def soh_from_throughput(
    pack: BatteryPack, config: BatteryConfig, throughput_kwh: float
) -> tuple[float, float]:
    """Return (soh, equivalent_full_cycles) for a cumulative throughput.

    EFC counts both charge and discharge throughput against usable energy, so a
    full discharge-charge round trip is two equivalent cycles. SOH falls
    linearly from 1.0 to 0.8 over ``cycle_life_to_80_soh`` cycles and is
    clamped at ``soh_floor``.
    """
    efc = throughput_kwh / pack.usable_energy_kwh
    soh = max(config.soh_floor, 1.0 - 0.2 * efc / float(pack.cycle_life_to_80_soh))
    return soh, efc


def detect_faults(
    pack: BatteryPack, config: BatteryConfig, state: BatteryState
) -> tuple[BatteryFaultCode, ...]:
    """Deterministic rule-based fault indicators for one state."""
    faults: list[BatteryFaultCode] = []
    if state.temperature_k > pack.max_operating_temperature_k:
        faults.append(BatteryFaultCode.OVER_TEMPERATURE)
    if state.temperature_k < pack.min_operating_temperature_k:
        faults.append(BatteryFaultCode.UNDER_TEMPERATURE)
    max_discharge_a = pack.max_discharge_c_rate * pack.nominal_capacity_ah
    max_charge_a = pack.max_charge_c_rate * pack.nominal_capacity_ah
    if state.current_a > max_discharge_a or state.current_a < -max_charge_a:
        faults.append(BatteryFaultCode.OVER_CURRENT)
    if state.voltage_v > pack.max_voltage_v:
        faults.append(BatteryFaultCode.OVER_VOLTAGE)
    if state.voltage_v < pack.min_voltage_v:
        faults.append(BatteryFaultCode.UNDER_VOLTAGE)
    if state.soc < 0.0 or state.soc > 1.0:
        faults.append(BatteryFaultCode.SOC_OUT_OF_BOUNDS)
    if state.soh < config.soh_fault_threshold:
        faults.append(BatteryFaultCode.SEVERE_DEGRADATION)
    return tuple(faults)


class BatteryModel:
    """Deterministic per-interval battery model.

    ``step`` is a pure transition: given a state, power, and timestep it returns
    the next state. No randomness, no global state.
    """

    def __init__(self, pack: BatteryPack, config: BatteryConfig) -> None:
        self._pack = pack
        self._config = config

    def initial_state(
        self, *, soc: float = 1.0, soh: float = 1.0, time_s: float = 0.0
    ) -> BatteryState:
        if not 0.0 <= soc <= 1.0:
            raise ValueError(f"initial soc {soc!r} outside [0, 1]")
        if not 0.0 < soh <= 1.0:
            raise ValueError(f"initial soh {soh!r} outside (0, 1]")
        ocv = ocv_voltage(self._pack, soc)
        state = BatteryState(
            time_s=time_s,
            soc=soc,
            soh=soh,
            voltage_v=ocv,
            current_a=0.0,
            power_kw=0.0,
            temperature_k=self._config.ambient_temperature_k,
            throughput_kwh=0.0,
            equivalent_full_cycles=0.0,
        )
        return state.model_copy(update={"faults": detect_faults(self._pack, self._config, state)})

    def step(
        self,
        state: BatteryState,
        *,
        power_kw: float,
        dt_s: float,
        soc_bounds: tuple[float, float] = (0.0, 1.0),
    ) -> BatteryState:
        """Advance one timestep given net battery power (positive discharges)."""
        if dt_s <= 0.0:
            raise ValueError(f"timestep must be positive, got {dt_s!r}")
        soc_min, soc_max = soc_bounds
        if not soc_min < soc_max:
            raise ValueError(f"invalid soc bounds {soc_bounds!r}")

        dt_h = dt_s / SECONDS_PER_HOUR
        usable_kwh = self._pack.usable_energy_kwh

        ocv = ocv_voltage(self._pack, state.soc)
        current_a, power_limited = current_from_power(
            ocv, self._config.internal_resistance_ohm, power_kw * 1000.0
        )
        voltage_v = ocv - current_a * self._config.internal_resistance_ohm

        soc = state.soc - power_kw * dt_h / usable_kwh
        soc_limited = False
        if soc < soc_min:
            soc, soc_limited = soc_min, True
        elif soc > soc_max:
            soc, soc_limited = soc_max, True

        heat_w = current_a * current_a * self._config.internal_resistance_ohm
        temperature_k = thermal_step(self._config, state.temperature_k, heat_w, dt_s)

        throughput_kwh = state.throughput_kwh + abs(power_kw) * dt_h
        soh, efc = soh_from_throughput(self._pack, self._config, throughput_kwh)

        next_state = BatteryState(
            time_s=state.time_s + dt_s,
            soc=soc,
            soh=soh,
            voltage_v=voltage_v,
            current_a=current_a,
            power_kw=power_kw,
            temperature_k=temperature_k,
            throughput_kwh=throughput_kwh,
            equivalent_full_cycles=efc,
            power_limited=power_limited,
            soc_limited=soc_limited,
        )
        faults = detect_faults(self._pack, self._config, next_state)
        return next_state.model_copy(update={"faults": faults})


class BatterySimulator:
    """Runs the battery model over an existing powertrain result.

    The powertrain stays authoritative for energy and SOC; this layer adds the
    electrical, thermal, degradation, and fault view. Deterministic: it only
    consumes the powertrain trace and needs no randomness.
    """

    def __init__(self, variant: VehicleVariant, config: BatteryConfig | None = None) -> None:
        self._pack = variant.battery_pack
        self._config = config if config is not None else BatteryConfig()

    def simulate(
        self,
        powertrain: PowertrainSimulation,
        *,
        initial_soc: float | None = None,
        initial_soh: float = 1.0,
    ) -> BatterySimulationResult:
        model = BatteryModel(self._pack, self._config)
        soc_min = float(powertrain.run.config.get("powertrain_config", {}).get("soc_min", 0.0))
        soc_max = float(powertrain.run.config.get("powertrain_config", {}).get("soc_max", 1.0))
        start_soc = (
            initial_soc
            if initial_soc is not None
            else float(powertrain.run.config.get("initial_soc", 1.0))
        )
        if not soc_min <= start_soc <= soc_max:
            raise ValueError(f"initial_soc {start_soc!r} outside powertrain bounds")

        state = model.initial_state(soc=start_soc, soh=initial_soh)
        points: list[BatteryStatePoint] = []
        max_soc_error = 0.0
        max_temperature_k = state.temperature_k
        min_temperature_k = state.temperature_k
        max_abs_current_a = 0.0
        fault_counts: dict[str, int] = {}
        initial_temperature_k = state.temperature_k

        for point in powertrain.result.trajectory:
            dt_s = point.time_s - state.time_s
            state = model.step(
                state, power_kw=point.battery_power_kw, dt_s=dt_s, soc_bounds=(soc_min, soc_max)
            )
            soc_error = abs(state.soc - point.soc)
            max_soc_error = max(max_soc_error, soc_error)
            max_temperature_k = max(max_temperature_k, state.temperature_k)
            min_temperature_k = min(min_temperature_k, state.temperature_k)
            max_abs_current_a = max(max_abs_current_a, abs(state.current_a))
            for fault in state.faults:
                fault_counts[fault.value] = fault_counts.get(fault.value, 0) + 1
            points.append(
                BatteryStatePoint(
                    time_s=point.time_s,
                    soc=state.soc,
                    soh=state.soh,
                    voltage_v=state.voltage_v,
                    current_a=state.current_a,
                    power_kw=state.power_kw,
                    temperature_k=state.temperature_k,
                    throughput_kwh=state.throughput_kwh,
                    equivalent_full_cycles=state.equivalent_full_cycles,
                    soc_error=soc_error,
                    faults=state.faults,
                    power_limited=state.power_limited,
                )
            )

        summary = BatterySummary(
            source_run_id=powertrain.run.run_id,
            initial_temperature_k=initial_temperature_k,
            final_soc=state.soc,
            final_soh=state.soh,
            final_temperature_k=state.temperature_k,
            max_temperature_k=max_temperature_k,
            min_temperature_k=min_temperature_k,
            max_absolute_current_a=max_abs_current_a,
            throughput_kwh=state.throughput_kwh,
            equivalent_full_cycles=state.equivalent_full_cycles,
            max_soc_error=max_soc_error,
            fault_counts=fault_counts,
        )
        return BatterySimulationResult(summary=summary, trajectory=tuple(points))
