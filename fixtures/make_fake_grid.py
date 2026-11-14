"""Generate fixtures/fake_grid.json: fake, spatially smooth stock features on H3 res-8 cells around Houston.

Every value here is invented. It exists so the web map, geo fixture mode and agent fixtures have a realistic-looking
grid before the real Census / CMS build lands. Run: PYTHONPATH=. python fixtures/make_fake_grid.py
"""

import json
import math
import random
from collections.abc import Sequence
from pathlib import Path

import h3

RES = 8
LON_MIN, LON_MAX = -95.85, -94.95
LAT_MIN, LAT_MAX = 29.45, 30.20
OUT = Path(__file__).parent / "fake_grid.json"

DOWNTOWN = (29.760, -95.370)
MED_CENTER = (29.707, -95.401)

# (lat, lon, amplitude in people/km², spread in km): real places so the map reads as Houston.
URBAN_CENTERS = [
    (29.760, -95.370, 3200, 9),  # Downtown / Midtown
    (29.740, -95.460, 2600, 6),  # Galleria / Uptown
    (29.707, -95.401, 2200, 5),  # Texas Medical Center
    (29.700, -95.530, 2400, 7),  # Sharpstown / Gulfton
    (29.690, -95.200, 1800, 7),  # Pasadena
    (29.600, -95.620, 1600, 8),  # Sugar Land
    (29.780, -95.780, 1500, 8),  # Katy
    (29.560, -95.290, 1500, 7),  # Pearland
    (29.970, -95.640, 1500, 8),  # Cypress
    (30.170, -95.470, 1400, 8),  # The Woodlands
    (29.990, -95.270, 1300, 7),  # Humble / Atascocita
    (29.850, -95.260, 1300, 6),  # Northeast Houston
    (29.660, -95.110, 900, 6),  # Deer Park / La Porte
]

# Higher-income pockets (lat, lon, amplitude in USD, spread in km).
INCOME_CENTERS = [
    (29.750, -95.430, 70000, 4),  # River Oaks / West U
    (30.170, -95.470, 55000, 9),  # The Woodlands
    (29.600, -95.620, 45000, 8),  # Sugar Land
    (29.780, -95.780, 35000, 9),  # Katy
    (29.560, -95.290, 25000, 7),  # Pearland
    (29.770, -95.560, 40000, 5),  # Memorial
]


def km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Haversine distance in km."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(a))


def bump(lat: float, lon: float, centers: Sequence[tuple[float, float, float, float]]) -> float:
    """Sum of Gaussian bumps."""
    return sum(amp * math.exp(-((km(lat, lon, c_lat, c_lon) / spread) ** 2)) for c_lat, c_lon, amp, spread in centers)


def random_field(rng: random.Random, n: int, amp: float, spread: tuple[float, float]) -> list[tuple[float, float, float, float]]:
    """Random smooth field: n bumps with amplitudes in [-amp, amp]."""
    return [
        (rng.uniform(LAT_MIN, LAT_MAX), rng.uniform(LON_MIN, LON_MAX), rng.uniform(-amp, amp), rng.uniform(*spread))
        for _ in range(n)
    ]


def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def cells_in_box() -> list[str]:
    box = h3.LatLngPoly([(LAT_MIN, LON_MIN), (LAT_MIN, LON_MAX), (LAT_MAX, LON_MAX), (LAT_MAX, LON_MIN)])
    return sorted(h3.polygon_to_cells(box, RES))


def main() -> None:
    random.seed(42)
    rng = random.Random(42)
    density_noise = random_field(rng, 25, 0.35, (4, 12))  # multiplicative
    kids_noise = random_field(rng, 15, 0.06, (6, 15))
    older_noise = random_field(rng, 15, 0.06, (6, 15))
    income_noise = random_field(rng, 20, 12000, (5, 12))
    growth_noise = random_field(rng, 15, 3.0, (6, 15))
    supply_noise = random_field(rng, 15, 0.4, (5, 10))

    rows = []
    for cell in cells_in_box():
        lat, lon = h3.cell_to_latlng(cell)
        area = h3.cell_area(cell, "km^2")
        d_core = km(lat, lon, *DOWNTOWN)
        d_tmc = km(lat, lon, *MED_CENTER)

        density = (40 + bump(lat, lon, URBAN_CENTERS) + 600 * math.exp(-d_core / 25)) * (
            1 + bump(lat, lon, density_noise)
        )
        density = clamp(density * rng.uniform(0.9, 1.1), 15, 9000)
        pop_total = density * area

        # Age mix: suburbs have more kids, a few areas skew older, the core skews 18-44.
        suburb = math.exp(-(((d_core - 30) / 15) ** 2))
        s_0_17 = clamp(0.20 + 0.08 * suburb + bump(lat, lon, kids_noise), 0.10, 0.36)
        s_65 = clamp(0.12 + 0.02 * (1 - suburb) + bump(lat, lon, older_noise), 0.05, 0.28)
        s_45_64 = clamp(0.24 + 0.4 * (s_65 - 0.12) + rng.uniform(-0.01, 0.01), 0.15, 0.34)
        s_18_44 = 1 - s_0_17 - s_65 - s_45_64

        income = 42000 + bump(lat, lon, INCOME_CENTERS) + bump(lat, lon, income_noise)
        income -= 12000 * math.exp(-(((lon + 95.25) / 0.12) ** 2)) * math.exp(-(((lat - 29.75) / 0.1) ** 2))  # east side
        income = clamp(income * rng.uniform(0.95, 1.05), 22000, 200000)

        # Insurance shares sum to 1: Medicaid and uninsured fall with income, Medicare rises with the 65+ share.
        inc = (income - 22000) / 120000
        medicaid = clamp(0.28 - 0.25 * inc + 0.25 * (s_0_17 - 0.2), 0.03, 0.40)
        uninsured = clamp(0.24 - 0.20 * inc, 0.04, 0.32)
        medicare = clamp(0.9 * s_65 + 0.02, 0.05, 0.30)
        private = max(0.10, 1 - medicaid - uninsured - medicare)
        total = medicaid + uninsured + medicare + private
        medicaid, uninsured, medicare, private = (x / total for x in (medicaid, uninsured, medicare, private))

        growth = -1.5 + 14 * math.exp(-(((d_core - 35) / 12) ** 2)) + bump(lat, lon, growth_noise)
        growth = round(growth + rng.uniform(-1, 1), 2)

        supply = 1 + bump(lat, lon, supply_noise)
        primary_care = round(clamp((bump(lat, lon, URBAN_CENTERS) / 18 + 400 * math.exp(-d_core / 12)) * supply * 0.6, 0, 800))
        ortho = round(clamp((180 * math.exp(-d_tmc / 8) + bump(lat, lon, URBAN_CENTERS) / 70) * supply, 0, 300))

        pops = {
            "pop_0_17": pop_total * s_0_17,
            "pop_18_44": pop_total * s_18_44,
            "pop_45_64": pop_total * s_45_64,
            "pop_65plus": pop_total * s_65,
        }
        pops["pop_total"] = sum(pops.values())

        row: dict[str, object] = {"h3": cell, "lat": round(lat, 6), "lon": round(lon, 6)}
        for key in ("pop_total", "pop_0_17", "pop_18_44", "pop_45_64", "pop_65plus"):
            row[key] = round(pops[key])
            row[f"{key}_density"] = round(pops[key] / area, 1)
        row["pop_45plus_density"] = round((pops["pop_45_64"] + pops["pop_65plus"]) / area, 1)
        row["ins_medicare_share"] = round(medicare, 4)
        row["ins_medicaid_share"] = round(medicaid, 4)
        row["ins_private_share"] = round(private, 4)
        row["ins_uninsured_share"] = round(uninsured, 4)
        row["income_median"] = round(income, -2)
        row["pop_growth"] = growth
        row["primary_care_15m"] = primary_care
        row["ortho_supply_20m"] = ortho
        rows.append(row)

    OUT.write_text(json.dumps(rows, separators=(",", ":")) + "\n")
    print(f"wrote {len(rows)} cells to {OUT}")


if __name__ == "__main__":
    main()
