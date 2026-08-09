# EV powertrain — SIMPLIFIED MODEL

This document specifies the powertrain subsystem in `autoforge/services/vehicle/powertrain.py`.
It is an educational simulation, not a vehicle model. Every simplification is
listed at the end; anything not listed here should be treated as a bug.

## Scope

Given a validated driving profile (`DrivingScenario`: time / speed / grade
samples) and a validated `VehicleVariant` (mass, drag, rolling resistance,
battery, motor) plus a `PowertrainConfig`, the subsystem produces per-interval
wheel and battery power, energy consumed/recovered, SOC, and a summary
(distance, consumption, estimated range, peak powers, power-limited time).

The trajectory is prescribed by the scenario — the model never re-derives
motion from forces. Forces are used only to compute the power the wheels must
deliver to follow the profile.

## Units

- SI internally: meters, seconds, kilograms, watts.
- Field names carry explicit unit suffixes: `_m`, `_m2`, `_kg`, `_kw`, `_kwh`,
  `_mps`, `_kmh`.
- Energy is reported in kWh; power in kW; consumption in kWh/km.

## Longitudinal force

Per interval, using the interval-average speed `v` and acceleration
`a = (v1 - v0)/dt` (forward finite difference between the two samples):

```
theta   = arctan(grade)                     # grade = rise / run
sin_t   = grade / sqrt(1 + grade^2)         # exact sin(theta)
cos_t   = 1 / sqrt(1 + grade^2)             # exact cos(theta)
m_total = kerb_mass_kg + cargo_mass_kg

F_drag     = 0.5 * rho * Cd * A * v^2
F_rolling  = Crr * m_total * g * cos_t
F_grade    = m_total * g * sin_t
F_inertia  = m_total * a

F_total = F_drag + F_rolling + F_grade + F_inertia     # N, signed
P_wheel = F_total * v                                   # W, signed
```

`P_wheel > 0` means the wheels demand traction; `P_wheel < 0` means the vehicle
is slowing and regen is (possibly) available.

## Traction path

```
P_demand   = P_wheel / (motor_eff * drivetrain_eff)     # battery-side demand
P_traction = min(P_demand, motor_peak_power_kw * 1000)  # motor peak clamp
battery_draw = P_traction + P_aux                       # aux always drawn
battery_draw = min(battery_draw, max_discharge_c_rate * nominal_energy_kwh * 1000)
```

`power_limited` is set when `P_traction < P_demand` (motor clamp or the
battery draw cap), or when the battery cannot supply the requested energy.

## SOC and depletion

Per interval the requested energy is `E = battery_draw * dt`. Available energy
above the floor is `E_avail = (soc - soc_min) * usable_energy_kwh`. If
`E > E_avail`, the vehicle is `depleted` and `power_limited`: only the
available energy is delivered, `soc` is set to `soc_min`, and a single
`battery_depleted` event is emitted on the first such interval. Otherwise:

```
soc_new = soc - E / usable_energy_kwh
```

SOC never goes below `soc_min` or above `soc_max`; the discharge clamp in the
code also protects against a max C-rate with zero available energy.

## Regen path

When `P_wheel < 0`, recoverable power is

```
P_recover = -P_wheel * motor_eff * drivetrain_eff * regen_eff
```

clamped by:

1. `max_regen_power_kw * 1000` — regen hardware limit;
2. `max_charge_c_rate * nominal_energy_kwh * 1000` — charge C-rate limit;
3. SOC headroom, including the concurrent auxiliary draw.

The auxiliary load still must be fed during regen. The SOC after recovering
`E_r` while drawing aux `P_aux` for `dt` is
`soc + (E_r - P_aux*dt) / usable_energy_kwh`, which must stay at or below
`soc_max`. When the headroom is smaller than the recoverable energy, only the
aux-offset portion is stored and the rest is **discarded and counted in
`regen_discarded_kwh`**. Regen energy is never created — a battery at
`soc_max` cannot take energy, so nothing is recovered beyond the concurrent
aux draw.

## Energy conservation

In every interval the bookkeeping obeys

```
(soc_new - soc_old) * usable_energy_kwh == -(battery_draw - recovered) * dt
```

that is, the SOC change times usable energy exactly equals net energy
(consumed minus recovered), including aux. This invariant is asserted in
`autoforge/tests/test_powertrain_integration.py`.

## Result summary

- `distance_km` — trapezoidal integration of speed over time.
- `energy_consumed_kwh` — gross battery draw over the run (traction + aux on
  discharge intervals, aux on regen intervals).
- `energy_recovered_kwh` — stored regen (only what the battery actually took).
- `net_energy_kwh` — `consumed - recovered` (positive when driving).
- `average_consumption_kwh_per_km` — consumed / distance; `None` when the
  vehicle never moves. Negative net energy over a run (more regen than use)
  yields `None` for range.
- `estimated_range_km` — `usable_energy_kwh / average_consumption_kwh_per_km`
  (net), `None` if undefined. This is a simplified division, not a certified
  or real-world figure.
- `power_limited_seconds` — cumulative time the powertrain could not deliver
  the requested power.
- `peak_power_kw` / `peak_regen_power_kw` — max battery draw / stored regen
  power over the run.

## Reference scenario

Demo vehicle (`build_demo_variant`): kerb 1900 kg, Cd 0.23, A 2.3 m2,
motor peak 230 kW, usable 75 kWh, nominal 80 kWh, max C-rate 4.
Default `PowertrainConfig`: g 9.81, rho 1.225, Crr 0.011, motor_eff 0.92,
drivetrain_eff 0.95, regen_eff 0.65, aux 0.6 kW, max_regen 80 kW.

Reference cycle (`reference_highway_cycle`): 10 min at a constant 30 m/s
(108 km/h) on flat road, dt = 1 s.

Hand-derived values (all flat, constant speed, so every interval is identical):

```
F_drag    = 0.5 * 1.225 * 0.23 * 2.3 * 30^2 = 291.611 N
F_rolling = 0.011 * 1900 * 9.81            = 205.029 N
F_total   = 496.640 N
P_wheel   = 496.640 * 30                   = 14.899 kW
P_traction= 14899.2 / (0.92 * 0.95)        = 17.047 kW
P_battery = 17.047 + 0.6                   = 17.647 kW
E_per_s   = 17.647 / 3600                  = 0.00490199 kWh
E_total   = 0.00490199 * 600               = 2.941 kWh
distance  = 30 * 600 / 1000                = 18.00 km
consumption = 2.941 / 18.00                = 0.1634 kWh/km
range     = 75 / 0.1634                     = 459 km
final SOC = 1 - 2.941 / 75                 = 0.9608 (96.1%)
peak power = 17.647 kW
```

These are asserted in `autoforge/tests/test_powertrain_integration.py`
(`TestReferenceScenario`).

## Validation

- `DrivingScenario` rejects fewer than 2 samples, mismatched tuple lengths,
  non-zero start time, non-finite values, negative speed or speed above
  `MAX_SPEED_MPS` (100 m/s), grade outside [-1, 1], and non-uniform positive
  timestep.
- `PowertrainConfig` rejects `soc_min >= soc_max`.
- The simulator rejects an initial SOC outside `[soc_min, soc_max]`.
- `BatteryPack` requires `0 < usable_energy_kwh <= nominal_energy_kwh`.

## Simplifications (all `SIMPLIFIED MODEL`)

- Constant motor, drivetrain, and regen efficiencies; no efficiency maps, no
  temperature or voltage dependence.
- Constant auxiliary load; no HVAC/thermal management.
- One-mass vehicle; no wheel slip, suspension, or driveline dynamics.
- Battery as a single energy reservoir: no cells, no open-circuit voltage,
  no internal resistance or heat, no degradation, no cell imbalance.
- Regen has fixed efficiency and power/C-rate caps; no blended-braking or
  brake-force split; braking below zero stored energy is simply discarded.
- Estimated range is a linear division, explicitly not a certification figure.
- Trajectory is prescribed by the scenario and never re-derived from forces.
