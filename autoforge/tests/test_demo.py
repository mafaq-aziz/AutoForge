"""Smoke tests for the foundation demo (importable core)."""

from autoforge.apps.simulation.demo import build_demo_variant, run_demo


def test_demo_variant_is_physically_sane() -> None:
    variant = build_demo_variant()
    assert 1000.0 < variant.kerb_mass_kg < 3000.0
    assert 300.0 < variant.battery_pack.nominal_voltage_v < 500.0
    assert 0.1 < variant.motor.peak_efficiency < 1.0


def test_demo_runs_and_logs_expected_events() -> None:
    result = run_demo(seed=42, days=30.0)
    assert result.company.name == "AutoForge Motors"
    assert result.run.seed == 42
    assert result.run.steps == 30
    # 30 ticks + 1 scheduled mid-run event
    assert result.run.events_logged == 31
    tick_times = {entry["time_days"] for entry in result.events if entry["event"] == "tick"}
    assert tick_times == {float(day) for day in range(1, 31)}
    mid_run = [e for e in result.events if e["event"] == "mid_run"]
    assert len(mid_run) == 1
    assert mid_run[0]["time_days"] == 15.0


def test_demo_is_reproducible_for_same_seed() -> None:
    a = run_demo(seed=7, days=10.0)
    b = run_demo(seed=7, days=10.0)
    assert a.events == b.events
