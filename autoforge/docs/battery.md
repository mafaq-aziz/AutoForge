# Battery / BMS — SIMPLIFIED MODEL

This document specifies the battery subsystem in `autoforge/services/battery/`
(`config.py`, `model.py`, `result.py`). It is an educational simulation, not a
real battery management system. Every simplification is listed at the end;
anything not listed here should be treated as a bug.

## Scope

The battery layer consumes an existing powertrain result
(`PowertrainSimulation`), which stays **authoritative for SOC and energy**, and
adds the per-interval electrical (current, voltage, IR drop), lumped thermal
(I²R heat, Newton cooling), degradation (energy-throughput SOH baseline), and
deterministic fault view. It produces a typed `BatterySimulationResult`
(a `BatterySummary` plus a trajectory of `BatteryStatePoint`).

The battery SOC integrated by this model is a **consistency check**, not a
second source of truth: the summary reports `max_soc_error`, the largest
`|model SOC − powertrain SOC|`, which stays at float-rounding level because both
integrations use the same net battery power and the same SOC clamps.

## Units

- SI internally: kelvin for temperature, ohm for resistance, watts/amperes for
  current and power, joules for thermal capacity.
- Power is reported in kW (positive = discharging), current in amperes
  (positive = discharging), energy in kWh, temperature in kelvin.
- Every field name carries its unit suffix (`_kw`, `_kwh`, `_k`, `_a`, `_ohm`,
  `_j_per_k`, `_w_per_k`).

## Electrical model

Linear open-circuit voltage between the pack voltage bounds:

```
OCV(soc) = V_min + soc * (V_max - V_min)
```

Given requested power `P` (positive discharges) and pack resistance `R`:

```
R * I^2 - OCV * I + P = 0        # V = OCV - I*R  and  P = V*I
I = (OCV - sqrt(OCV^2 - 4*R*P)) / (2*R)
V = OCV - I*R
```

On that physical branch `V*I == P` exactly. If `OCV^2 < 4*R*P` the pack cannot
deliver the requested power at this voltage; the current is clamped to the
maximum-power point `I = OCV/(2R)` and `power_limited` is set (rather than the
model silently accepting an impossible value). `V*I == P` no longer holds when
clamped.

## SOC integration

```
soc_new = soc - P * dt_h / usable_energy_kwh
```

clamped to `[soc_min, soc_max]` (the bounds passed at each step; the simulator
passes the powertrain's `soc_min`/`soc_max`). `soc_limited` is set when the
clamp fires. The soc error in the summary is the difference between this
integration and the powertrain's; both use the same power and clamps, so it is
~0 (asserted `< 1e-6` in tests).

## Thermal model (lumped)

```
dT/dt = (I^2*R - k_cool * (T - T_ambient)) / C_th
```

`C_th` is the thermal capacity (J/K), `k_cool` the Newton-style cooling
coefficient (W/K). Steady state is reached when `I^2*R == k_cool*(T − T_amb)`,
i.e. `T = T_amb + I^2*R / k_cool`. There is no active cooling model beyond this
single coefficient, no thermal gradient across the pack, and no state-of-charge
or age dependence of `R` or `C_th`.

## Degradation (SIMPLIFIED DEGRADATION MODEL)

```
throughput_kwh += |P| * dt
EFC = throughput_kwh / usable_energy_kwh
SOH = clamp(1 - 0.2 * EFC / cycle_life_to_80_soh, soh_floor, 1)
```

`EFC` counts throughput in both directions, so one full discharge-charge round
trip is two equivalent cycles. SOH is a purely energy-throughput baseline: real
aging depends on chemistry, temperature history, calendar time, charge profile,
and manufacturing variance, none of which is modeled. `soh_floor` prevents SOH
running away below a sane minimum; `soh_fault_threshold` (> `soh_floor`) is
where the `severe_degradation` fault fires.

## Faults

Faults are deterministic rule checks on each state (see
`detect_faults` in `model.py`). There is no ML and no synthetic fault data:
a fault fires only when its rule actually triggers.

| Fault | Rule |
| --- | --- |
| `over_temperature` | `T > max_operating_temperature_k` |
| `under_temperature` | `T < min_operating_temperature_k` |
| `over_current` | `I > max_discharge_c_rate * nominal_capacity_ah` or `I < -max_charge_c_rate * nominal_capacity_ah` |
| `over_voltage` | `V > max_voltage_v` (e.g. charging a full battery) |
| `under_voltage` | `V < min_voltage_v` |
| `soc_out_of_bounds` | `soc < 0` or `soc > 1` (defensive; the clamp prevents this) |
| `severe_degradation` | `soh < soh_fault_threshold` |

The `BatteryFaultCode` enum in `autoforge/domain/battery_state.py` is the
transparent baseline that future anomaly-detection work can be evaluated
against.

## Hand calculation (reference case)

Demo pack (`build_demo_variant`): usable 75 kWh, 300–450 V (OCV linear), pack
resistance 0.05 Ω, `cycle_life_to_80_soh` 1500. Default `BatteryConfig`:
`ambient_temperature_k` 298.15, `thermal_capacity_j_per_k` 100 000,
`cooling_coefficient_w_per_k` 50, `soh_floor` 0.6, `soh_fault_threshold` 0.8.

One 600 s step at 50 kW from a full battery:

```
OCV   = 450 V
disc  = 450^2 - 4*0.05*50e3 = 192 500
I     = (450 - sqrt(192500)) / (2*0.05) = 112.52 A
V     = 450 - 0.05*112.52 = 444.37 V          (V*I = 50 kW exactly)
heat  = I^2*R = 112.52^2 * 0.05 = 633.0 W
dT    = 633.0 * 600 / 100 000 = 3.798 K       -> T = 301.95 K
dSOC  = 50 * (600/3600) / 75 = 0.1111         -> SOC = 0.8889
E_thr = 50 * (600/3600) = 8.333 kWh           -> EFC = 0.1111
SOH   = 1 - 0.2 * 0.1111 / 1500 = 0.99999     (no degradation fault)
```

Asserted in `autoforge/tests/test_battery.py` (`TestModelStep::test_hand_calc_step`).

## Validation

- `BatteryConfig` requires `soh_fault_threshold > soh_floor`; temperature,
  resistance, thermal capacity, and coefficients are bounded.
- `BatteryState` (domain) requires `soc` in [0, 1], `soh` in (0, 1], positive
  voltage/temperature; `BatteryStatePoint` and `BatterySummary` mirror those
  bounds on the typed outputs.
- `BatteryModel.step` rejects non-positive timesteps and inverted SOC bounds;
  `initial_state` rejects SOC outside [0, 1] and SOH outside (0, 1].
- `BatterySimulator.simulate` rejects an initial SOC outside the powertrain's
  `[soc_min, soc_max]`.

## Determinism

The battery layer draws no randomness: identical variant, battery config,
powertrain result, and software version reproduce identical results. Tested by
`TestBatterySimulator::test_reproducible_for_same_seed`.

## Simplifications (all `SIMPLIFIED MODEL`)

- Linear OCV vs SOC; no electrochemical curve, hysteresis, or temperature
  dependence of OCV.
- Constant pack resistance; no temperature, SOC, age, or per-cell dependence.
- Lumped single-node thermal mass; single cooling coefficient; no coolant,
  fins, fans, or pack geometry; no thermal imbalance across cells.
- Linear energy-throughput SOH; no calendar aging, temperature/aging coupling,
  charge-rate effect, or cell-to-cell variance.
- Battery modeled as one aggregate pack; `cells_in_series`/`cells_in_parallel`
  only set the nominal capacity used for C-rate current limits.
- Faults are threshold rules on aggregated pack quantities, not cell-level
  detection; no diagnosis, prognosis, or balancing.
- The model consumes the powertrain's net battery power per interval; it does
  not re-derive it and cannot change the powertrain's SOC or energy results.
