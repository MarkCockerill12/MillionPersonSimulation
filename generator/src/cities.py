"""Per-city configuration. Adding a city = adding one entry here.

Everything city-specific lives in this file: where the OSM data comes from, which metric
projection to use, address/phone formats, currency and pay level, how people get around,
and how dense housing is. The rest of the pipeline is city-agnostic.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np

# Western-international name mix (Latin script). Applied in every city.
WESTERN_NAMES = {
    "en_US": 0.27, "en_GB": 0.22, "en_IE": 0.08, "en_AU": 0.06, "en_CA": 0.06,
    "nl_NL": 0.09, "de_DE": 0.08, "fr_FR": 0.06, "it_IT": 0.03, "es_ES": 0.03, "sv_SE": 0.02,
}


@dataclass(frozen=True)
class ModeChoice:
    """Commute mode probabilities by trip type (each list sums to 1)."""
    walk_max_m: float
    short: dict[str, float]           # trips under walk_max_m
    far_with_car: dict[str, float]
    far_without_car: dict[str, float]
    car_ownership_base: float         # household car ownership at the centre…
    car_ownership_per_km: float       # …rising with distance from it
    car_ownership_max: float


@dataclass(frozen=True)
class City:
    key: str
    name: str
    country: str
    nominatim: str
    pbf_url: str
    metric_crs: int
    id_prefix: str
    currency: str
    salary_factor: float              # multiplier on the GBP reference salaries in people.OCCUPATIONS
    postcode: Callable[[np.ndarray, np.ndarray, np.ndarray, int], np.ndarray]
    phone: Callable[[np.random.Generator, int], list[str]]
    modes: ModeChoice
    mode_labels: dict[str, str]
    sqm_per_dwelling: float           # gross floor area per household in residential buildings
    default_levels: dict[str, int]
    name_locales: dict[str, float] = field(default_factory=lambda: dict(WESTERN_NAMES))
    email_domains: tuple[str, ...] = ("mailbox.example", "postbox.example", "inbox.example", "letter.example", "mail.example")


# ------------------------------------------------------------------ formats
NL_LETTERS = np.array(list("ABCDEGHJKLMNPRTVWXZ"))
UK_LETTERS = np.array(list("ABDEFGHJLNPQRSTUWXYZ"))


def nl_postcode(district_rank: np.ndarray, h1: np.ndarray, h2: np.ndarray, n_districts: int) -> np.ndarray:
    """Dutch format '1017 AB'. Amsterdam's real range is 1011–1109: spread districts across it."""
    base = 1011 + np.floor(district_rank * (98 / max(n_districts, 1))).astype(int)
    digits = base + (h1 % 3).astype(int)
    return np.char.add(np.char.add(digits.astype(str), " "),
                       np.char.add(NL_LETTERS[h2 % len(NL_LETTERS)], NL_LETTERS[(h2 // 19) % len(NL_LETTERS)]))


def uk_postcode(district_rank: np.ndarray, h1: np.ndarray, h2: np.ndarray, n_districts: int) -> np.ndarray:
    outward = np.char.add("B", (district_rank + 1).astype(str))
    inward = np.char.add((h1 % 9 + 1).astype(str), np.char.add(UK_LETTERS[h2 % 20], UK_LETTERS[(h2 // 20) % 20]))
    return np.char.add(np.char.add(outward, " "), inward)


def nl_phone(rng: np.random.Generator, n: int) -> list[str]:
    # 06-0 is not an allocated Dutch mobile block, so these cannot belong to anyone.
    d = rng.integers(0, 10_000_000, n)
    return [f"+31 6 0{x // 10000:03d} {x % 10000:04d}" for x in d]


def uk_phone(rng: np.random.Generator, n: int) -> list[str]:
    return [f"07700 900{x:03d}" for x in rng.integers(0, 1000, n)]  # Ofcom drama range


CITIES: dict[str, City] = {
    "amsterdam": City(
        key="amsterdam",
        name="Amsterdam",
        country="Netherlands",
        nominatim="Amsterdam, Noord-Holland, Netherlands",
        pbf_url="https://download.geofabrik.de/europe/netherlands/noord-holland-latest.osm.pbf",
        metric_crs=28992,                         # RD New (Dutch national grid, metres)
        id_prefix="AMS",
        currency="EUR",
        salary_factor=1.32,
        postcode=nl_postcode,
        phone=nl_phone,
        modes=ModeChoice(
            walk_max_m=1200,
            short={"walk": 0.45, "cycle": 0.50, "car": 0.05},
            far_with_car={"car": 0.34, "bus": 0.14, "train": 0.16, "cycle": 0.36},
            far_without_car={"bus": 0.30, "train": 0.26, "cycle": 0.40, "walk": 0.04},
            car_ownership_base=0.22, car_ownership_per_km=0.05, car_ownership_max=0.62,
        ),
        mode_labels={"walk": "Walk", "cycle": "Bike", "bus": "Tram or bus", "train": "Metro or train", "car": "Car"},
        sqm_per_dwelling=78,
        default_levels={"residential": 3, "apartments": 5, "commercial": 3, "industrial": 1, "education": 3, "healthcare": 4},
    ),
    "birmingham": City(
        key="birmingham",
        name="Birmingham",
        country="United Kingdom",
        nominatim="Birmingham, West Midlands, England",
        pbf_url="https://download.geofabrik.de/europe/united-kingdom/england/west-midlands-latest.osm.pbf",
        metric_crs=27700,                         # British National Grid
        id_prefix="BHM",
        currency="GBP",
        salary_factor=1.0,
        postcode=uk_postcode,
        phone=uk_phone,
        modes=ModeChoice(
            walk_max_m=1200,
            short={"walk": 0.55, "cycle": 0.05, "car": 0.40},
            far_with_car={"car": 0.72, "bus": 0.16, "train": 0.09, "cycle": 0.03},
            far_without_car={"bus": 0.72, "train": 0.14, "cycle": 0.06, "walk": 0.08},
            car_ownership_base=0.35, car_ownership_per_km=0.083, car_ownership_max=0.85,
        ),
        mode_labels={"walk": "Walk", "cycle": "Cycle", "bus": "Bus", "train": "Train", "car": "Car"},
        sqm_per_dwelling=90,
        default_levels={"residential": 2, "apartments": 4, "commercial": 2, "industrial": 1, "education": 2, "healthcare": 3},
    ),
}


def get(key: str) -> City:
    try:
        return CITIES[key.lower()]
    except KeyError:
        raise SystemExit(f"Unknown city '{key}'. Available: {', '.join(CITIES)}") from None
