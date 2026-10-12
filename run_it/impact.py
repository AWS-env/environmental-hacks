"""Turn measured energy into modelled CO2e and water. Every factor carries its source; env vars override."""
from __future__ import annotations

import os
from dataclasses import dataclass

J_PER_KWH = 3.6e6


@dataclass(frozen=True)
class Factor:
    value: float
    unit: str
    source: str


def _factor(env, default, unit, source) -> Factor:
    raw = os.environ.get(env)
    if raw:
        try:
            return Factor(float(raw), unit, f"{env} override")
        except ValueError:
            pass
    return Factor(default, unit, source)


GRID = _factor(
    "RUN_IT_GRID_KG_PER_KWH", 0.727, "kgCO2e/kWh",
    "CEA CO2 Baseline Database, all-India weighted average, FY2023-24")
WATER = _factor(
    "RUN_IT_WATER_L_PER_KWH", 2.4, "L/kWh",
    "electricity generation only: MoEFCC 2015 thermal-plant cap 3.0-3.5 m3/MWh x ~75% thermal share; "
    "excludes data-centre cooling and hydro evaporation")


def co2_grams(joules: float, grid: Factor = GRID) -> float:
    return joules / J_PER_KWH * grid.value * 1000


def water_ml(joules: float, water: Factor = WATER) -> float:
    return joules / J_PER_KWH * water.value * 1000


def fmt_energy(joules: float) -> str:
    if joules < 3600:
        return f"{joules:,.0f} J"
    if joules < J_PER_KWH:
        return f"{joules / 3600:,.2f} Wh"
    return f"{joules / J_PER_KWH:,.3f} kWh"


def fmt_mass(grams: float) -> str:
    if grams < 1:
        return f"{grams * 1000:.1f} mg"
    return f"{grams:.2f} g" if grams < 1000 else f"{grams / 1000:.2f} kg"


def fmt_volume(ml: float) -> str:
    if ml < 1:
        return f"~{ml * 1000:.0f} µL"
    return f"~{ml:.1f} mL" if ml < 1000 else f"~{ml / 1000:.2f} L"


def fmt_duration(seconds: float) -> str:
    minutes, secs = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m {secs:02d}s" if hours else f"{minutes}m {secs:02d}s"


def receipt(label: str, seconds: float, joules: float | None, measured_by: str, findings: str | None) -> str:
    """Plain-text summary printed after the dashboard closes; numbers keep their provenance tags."""
    lines = [f"run-it · {label} · {fmt_duration(seconds)}"]
    if joules is None:
        lines.append("  energy   not measured on this machine                [unavailable]")
    else:
        rows = [
            ("energy", f"{fmt_energy(joules)} ({joules / 3600:.3f} Wh)", f"measured: {measured_by}"),
            ("CO₂e", fmt_mass(co2_grams(joules)), f"modelled: {GRID.value} {GRID.unit}, {GRID.source}"),
            ("water", fmt_volume(water_ml(joules)), f"modelled: {WATER.value} {WATER.unit}, generation water"),
        ]
        lines += [f"  {name:<8} {value:<20} [{tag}]" for name, value, tag in rows]
    if findings:
        lines.append(f"  {'findings':<8} {findings:<20} [static: impact not measured]")
    return "\n".join(lines)
