"""Synthetic population: households, people, family links, jobs and schools.

Demographic distributions are loosely calibrated to European census figures (household size
mix, age structure, employment rate). City-specific behaviour comes from cities.py.
"""

from __future__ import annotations

import bisect
import unicodedata
from dataclasses import dataclass

import numpy as np
import pandas as pd
from faker import Faker
from scipy.spatial import cKDTree

REF_DATE = np.datetime64("2026-01-01")

from .cities import CITIES, City

# Set by configure(); defaults to the first configured city.
CITY: City = next(iter(CITIES.values()))
LOCALES: dict[str, float] = dict(CITY.name_locales)


def configure(city: City) -> None:
    """Point the population model at a city (names, formats, pay, travel behaviour)."""
    global CITY, LOCALES
    CITY = city
    LOCALES = dict(city.name_locales)


HOUSEHOLD_TYPES = {  # type -> probability
    "single": 0.29, "couple": 0.23, "couple_with_children": 0.23, "single_parent": 0.10,
    "multigenerational": 0.07, "shared": 0.08,
}

COLOURS = {"blue": 0.24, "green": 0.13, "purple": 0.11, "red": 0.09, "black": 0.08, "teal": 0.05,
           "orange": 0.05, "pink": 0.05, "yellow": 0.04, "grey": 0.03, "white": 0.03, "navy": 0.03,
           "turquoise": 0.02, "gold": 0.02, "maroon": 0.02, "lilac": 0.01}
BLOOD = {"O+": 0.35, "A+": 0.30, "B+": 0.08, "AB+": 0.02, "O-": 0.13, "A-": 0.08, "B-": 0.02, "AB-": 0.02}
EDU = ["none", "secondary", "upper_secondary", "vocational", "bachelors", "masters", "doctorate"]

# title, sector, median salary (GBP), minimum education index
OCCUPATIONS = [
    ("Retail Assistant", "Retail", 21500, 0), ("Warehouse Operative", "Logistics", 23500, 0),
    ("Delivery Driver", "Logistics", 26000, 0), ("Care Worker", "Health & Social Care", 22800, 0),
    ("Cleaner", "Facilities", 20500, 0), ("Chef", "Hospitality", 25000, 0),
    ("Bar Staff", "Hospitality", 19800, 0), ("Security Officer", "Facilities", 24000, 0),
    ("Bus Driver", "Transport", 31000, 0), ("Taxi Driver", "Transport", 25500, 0),
    ("Customer Service Advisor", "Business Services", 23500, 1), ("Administrator", "Business Services", 24500, 1),
    ("Hairdresser", "Personal Services", 21000, 1), ("Receptionist", "Business Services", 22000, 1),
    ("Electrician", "Construction", 36000, 3), ("Plumber", "Construction", 35000, 3),
    ("Mechanic", "Automotive", 30500, 3), ("Machine Operator", "Manufacturing", 27000, 1),
    ("Bricklayer", "Construction", 33000, 3), ("Production Manager", "Manufacturing", 45000, 2),
    ("Teaching Assistant", "Education", 21800, 2), ("Nurse", "Health & Social Care", 35500, 4),
    ("Police Officer", "Public Sector", 38000, 2), ("Paramedic", "Health & Social Care", 37500, 4),
    ("Sales Executive", "Business Services", 32000, 2), ("Accounts Clerk", "Finance", 26000, 2),
    ("Marketing Executive", "Business Services", 33000, 4), ("Teacher", "Education", 41000, 4),
    ("Accountant", "Finance", 48000, 4), ("Software Developer", "Technology", 52000, 4),
    ("Data Analyst", "Technology", 40000, 4), ("Civil Engineer", "Engineering", 47000, 4),
    ("Lawyer", "Legal", 58000, 4), ("Pharmacist", "Health & Social Care", 47500, 5),
    ("Architect", "Construction", 50000, 5), ("Doctor", "Health & Social Care", 72000, 5),
    ("University Lecturer", "Education", 52000, 6), ("Research Scientist", "Science", 46000, 6),
    ("IT Manager", "Technology", 62000, 4), ("Operations Director", "Business Services", 85000, 4),
]


@dataclass
class Population:
    households: pd.DataFrame
    persons: pd.DataFrame
    relationships: pd.DataFrame
    occupations: pd.DataFrame


def _pick(probs: dict[str, float], k: int, rng: np.random.Generator) -> np.ndarray:
    labels, p = _weighted(probs)
    return np.array(labels, dtype=object)[rng.choice(len(labels), k, p=p)]


def _weighted(d: dict) -> tuple[list, np.ndarray]:
    k = list(d)
    w = np.array([d[x] for x in k], dtype=float)
    return k, w / w.sum()


def _name_pools() -> dict[str, dict[str, np.ndarray]]:
    pools = {}
    for loc in LOCALES:
        prov = next(p for p in Faker(loc).factories[0].providers if ".person." in type(p).__module__)
        def grab(attr: str, fallback: str, prov=prov) -> np.ndarray:
            v = getattr(prov, attr, None) or getattr(prov, fallback, None) or prov.first_names
            return np.array(list(v.keys()) if isinstance(v, dict) else list(v), dtype=object)
        pools[loc] = {
            "M": grab("first_names_male", "first_names"),
            "F": grab("first_names_female", "first_names"),
            "L": grab("last_names", "last_names"),
        }
    return pools


def _ascii(s: str) -> str:
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower().replace(" ", "").replace("'", "")


# --------------------------------------------------------------------- composition
class _Draws:
    """Buffered random numbers for the per-household Python loop.

    numpy scalar calls cost ~1-20 µs each (rng.choice with p= is the worst); pulling from
    pre-generated Python lists costs ~50 ns. Over a million people this is the difference
    between minutes and seconds.
    """

    def __init__(self, rng: np.random.Generator, size: int = 1 << 20) -> None:
        self.rng, self.size = rng, size
        self._u: list[float] = []
        self._z: list[float] = []
        self._g: list[float] = []
        self.iu = self.iz = self.ig = 0

    def u(self) -> float:
        if self.iu >= len(self._u):
            self._u, self.iu = self.rng.random(self.size).tolist(), 0
        self.iu += 1
        return self._u[self.iu - 1]

    def z(self) -> float:
        if self.iz >= len(self._z):
            self._z, self.iz = self.rng.standard_normal(self.size).tolist(), 0
        self.iz += 1
        return self._z[self.iz - 1]

    def gamma(self) -> float:  # shape 4.5, scale 11: adult age above 18 for people living alone
        if self.ig >= len(self._g):
            self._g, self.ig = self.rng.gamma(4.5, 11, self.size // 4).tolist(), 0
        self.ig += 1
        return self._g[self.ig - 1]

    def normal(self, mu: float, sd: float) -> float:
        return mu + sd * self.z()

    def integers(self, lo: int, hi: int) -> int:
        return lo + int(self.u() * (hi - lo))

    def pick(self, cum: list[float]) -> int:
        return bisect.bisect_right(cum, self.u() * cum[-1])


def _clip(x: float, lo: float, hi: float) -> int:
    return int(min(max(x, lo), hi))


def _cum(p) -> list[float]:
    return np.cumsum(p).tolist()


def compose_households(n_people: int, rng: np.random.Generator):
    """Build household membership + intra-household relationships (no names yet)."""
    types, tp = _weighted(HOUSEHOLD_TYPES)
    _, lp = _weighted(LOCALES)
    type_cum, loc_cum = _cum(tp), _cum(lp)
    kids_cwc, kids_sp, kids_mg = _cum([0.38, 0.40, 0.15, 0.05, 0.02]), _cum([0.5, 0.35, 0.15]), _cum([0.35, 0.45, 0.2])
    sharers = _cum([0.35, 0.35, 0.2, 0.1])
    d = _Draws(rng)

    hh_type: list[str] = []
    hh_loc: list[int] = []
    p_hh: list[int] = []
    p_role: list[str] = []
    p_age: list[int] = []
    p_sex: list[str] = []
    p_family: list[int] = []      # surname group id (shared surname)
    p_loc: list[int] = []
    rels: list[tuple[int, int, str]] = []
    fam = 0

    def add(h: int, role: str, age: int, sex: str, family: int, loc: int) -> int:
        p_hh.append(h); p_role.append(role); p_age.append(int(age)); p_sex.append(sex)
        p_family.append(family); p_loc.append(loc)
        return len(p_hh) - 1

    def link(a: int, b: int, ab: str, ba: str) -> None:
        rels.append((a, b, ab)); rels.append((b, a, ba))

    def sex() -> str:
        return "F" if d.u() < 0.51 else "M"

    def couple(h: int, loc: int, age: int) -> tuple[int, int]:
        nonlocal fam
        s1 = sex()
        s2 = s1 if d.u() < 0.03 else ("M" if s1 == "F" else "F")
        a2 = _clip(age + d.normal(0, 3.5), 18, 99)
        fam += 1
        f1 = fam
        married = d.u() < 0.65
        if married and d.u() < 0.8:
            f2 = f1
        else:
            fam += 1
            f2 = fam
        a = add(h, "head", age, s1, f1, loc)
        b = add(h, "partner", a2, s2, f2, loc)
        link(a, b, "spouse" if married else "partner", "spouse" if married else "partner")
        return a, b

    def children(h: int, loc: int, parents: list[int], n: int, family: int) -> list[int]:
        youngest_parent = min(p_age[p] for p in parents)
        kids: list[int] = []
        first_gap = d.integers(20, 36)
        for i in range(n):
            age = youngest_parent - first_gap - i * d.integers(1, 4)
            if age < 0:
                break
            k = add(h, "child", age, sex(), family, loc)
            for p in parents:
                link(p, k, "parent", "child")
            for s in kids:
                link(s, k, "sibling", "sibling")
            kids.append(k)
        return kids

    h = 0
    while len(p_hh) < n_people:
        t = types[d.pick(type_cum)]
        loc = d.pick(loc_cum)
        if t == "single":
            age = d.gamma() + 18
            age = int(age if age <= 96 else 60 + d.u() * 32)  # resample the tail instead of piling it at the cap
            fam += 1
            add(h, "head", age, sex(), fam, loc)
        elif t == "couple":
            age = _clip(d.normal(30, 6) if d.u() < 0.5 else d.normal(64, 10), 19, 98)
            couple(h, loc, age)
        elif t == "couple_with_children":
            age = _clip(d.normal(41, 8), 22, 70)
            a, b = couple(h, loc, age)
            if not children(h, loc, [a, b], d.pick(kids_cwc) + 1, p_family[a]):
                t = "couple"
        elif t == "single_parent":
            fam += 1
            age = _clip(d.normal(38, 8), 19, 65)
            a = add(h, "head", age, "F" if d.u() < 0.86 else "M", fam, loc)
            children(h, loc, [a], d.pick(kids_sp) + 1, fam)
        elif t == "multigenerational":
            age = _clip(d.normal(42, 7), 26, 60)
            a, b = couple(h, loc, age)
            kids = children(h, loc, [a, b], d.pick(kids_mg) + 1, p_family[a])
            gage = min(100, p_age[a] + d.integers(24, 34))
            g1 = add(h, "parent", gage, sex(), p_family[a], loc)
            gps = [g1]
            if d.u() < 0.45:
                g2 = add(h, "parent", min(100, gage + d.integers(-4, 5)), "M" if p_sex[g1] == "F" else "F", p_family[a], loc)
                link(g1, g2, "spouse", "spouse")
                gps.append(g2)
            for g in gps:
                link(g, a, "parent", "child")
                for k in kids:
                    link(g, k, "grandparent", "grandchild")
        else:  # shared house of unrelated adults
            for i in range(d.pick(sharers) + 2):
                fam += 1
                add(h, "head" if i == 0 else "housemate", _clip(d.normal(25, 4), 18, 45), sex(), fam, d.pick(loc_cum))
        hh_type.append(t)
        hh_loc.append(loc)
        h += 1

    persons = pd.DataFrame({"hh": p_hh, "role": p_role, "age": p_age, "sex": p_sex,
                            "family": p_family, "loc": p_loc}).iloc[:n_people]
    rel = pd.DataFrame(rels, columns=["a", "b", "relationship"])
    rel = rel[(rel.a < n_people) & (rel.b < n_people)]
    n_hh = persons["hh"].iloc[-1] + 1
    households = pd.DataFrame({"type": hh_type[:n_hh], "loc": hh_loc[:n_hh]})
    return households, persons.reset_index(drop=True), rel.reset_index(drop=True)


def link_relatives_across_households(persons: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    """Adult children who moved out stay linked to their parents (and siblings still at home).

    The young adult's surname group is re-pointed at the parents' group so the
    family shares a surname across two addresses. Works on plain arrays: persons are
    stored contiguously by household, so a household's members are one slice.
    """
    role = persons["role"].to_numpy()
    age = persons["age"].to_numpy()
    family = persons["family"].to_numpy()
    loc = persons["loc"].to_numpy()
    hh = persons["hh"].to_numpy()
    hh_start = np.r_[0, np.flatnonzero(np.diff(hh)) + 1]
    hh_end = np.r_[hh_start[1:], len(hh)]

    is_head = role == "head"
    young = np.flatnonzero(is_head & (age >= 20) & (age <= 40))
    young = young[rng.random(len(young)) < 0.35]
    old = np.flatnonzero(is_head & (age >= 48) & (age <= 85))
    by_loc = {int(lv): old[loc[old] == lv] for lv in np.unique(loc[old])}
    draws = rng.random(len(young))

    used: set[int] = set()
    remap: dict[int, int] = {}
    pairs: list[tuple[int, int, str]] = []
    for idx, r in zip(young.tolist(), draws.tolist(), strict=True):
        cand = by_loc.get(int(loc[idx]))
        if cand is None or len(cand) == 0:
            continue
        ph = int(cand[int(r * len(cand))])
        if ph in used or age[ph] - age[idx] < 20:
            continue
        used.add(ph)
        remap[int(family[idx])] = int(family[ph])
        h = hh[ph]
        for m in range(hh_start[h], hh_end[h]):
            if role[m] in ("head", "partner"):
                pairs += [(m, idx, "parent"), (idx, m, "child")]
            elif role[m] == "child":
                pairs += [(m, idx, "sibling"), (idx, m, "sibling")]
    persons["family"] = persons["family"].map(remap).fillna(persons["family"]).astype(np.int64)
    return pd.DataFrame(pairs, columns=["a", "b", "relationship"])


# --------------------------------------------------------------------- names & identity
def assign_identity(persons: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    pools = _name_pools()
    locs = list(LOCALES)
    n = len(persons)
    first = np.empty(n, dtype=object)
    middle = np.empty(n, dtype=object)
    last = np.empty(n, dtype=object)

    # surname per family group, drawn from the family's locale
    fam_loc = persons.groupby("family")["loc"].first()
    fam_last = pd.Series(index=fam_loc.index, dtype=object)
    for li, loc in enumerate(locs):
        ids = fam_loc.index[fam_loc.to_numpy() == li]
        fam_last[ids] = rng.choice(pools[loc]["L"], len(ids))
    last[:] = fam_last.loc[persons["family"]].to_numpy()

    for li, loc in enumerate(locs):
        for s in ("M", "F"):
            m = ((persons["loc"] == li) & (persons["sex"] == s)).to_numpy()
            k = int(m.sum())
            first[m] = rng.choice(pools[loc][s], k)
            middle[m] = np.where(rng.random(k) < 0.6, rng.choice(pools[loc][s], k), None)

    df = pd.DataFrame({"first": first, "middle": middle, "last": last})
    for _ in range(30):  # enforce unique full names
        dup = df.duplicated(["first", "middle", "last"], keep="first").to_numpy() | (df["first"] == df["middle"]).to_numpy()
        if not dup.any():
            break
        for li, loc in enumerate(locs):
            for s in ("M", "F"):
                m = dup & (persons["loc"] == li).to_numpy() & (persons["sex"] == s).to_numpy()
                df.loc[m, "middle"] = rng.choice(pools[loc][s], int(m.sum()))
    persons["first_name"], persons["middle_name"], persons["last_name"] = df["first"], df["middle"], df["last"]

    # identifiers
    alphabet = np.array(list("ABCDEFGHJKLMNPQRSTUVWXYZ23456789"))
    codes = rng.choice(32 ** 8, n, replace=False) if n < 5_000_000 else np.arange(n) * 7919 % (32 ** 8)
    digits = np.stack([(codes // 32 ** i) % 32 for i in range(8)], axis=1)
    chars = alphabet[digits]
    persons["citizen_id"] = [CITY.id_prefix + "-" + "".join(c[:4]) + "-" + "".join(c[4:]) for c in chars]
    colours = rng.choice(256 ** 3, n, replace=False)
    persons["signature_colour"] = [f"#{c:06X}" for c in colours]

    domains = np.array(CITY.email_domains)
    base = [f"{_ascii(f)}.{_ascii(l)}" for f, l in zip(persons["first_name"], persons["last_name"])]
    base = pd.Series(base)
    suffix = base.groupby(base).cumcount()
    persons["email"] = (base + np.where(suffix > 0, suffix.astype(str), "") + "@" +
                        rng.choice(domains, n)).to_numpy()
    persons["phone"] = CITY.phone(rng, n)  # fictional number ranges, see cities.py

    age = persons["age"].to_numpy()
    days = rng.integers(0, 365, n)
    persons["date_of_birth"] = (REF_DATE - (age * 365.25).astype("timedelta64[D]") - days.astype("timedelta64[D]")).astype("datetime64[D]")

    ck, cp = _weighted(COLOURS)
    persons["favourite_colour"] = rng.choice(ck, n, p=cp)
    bk, bp = _weighted(BLOOD)
    persons["blood_type"] = rng.choice(bk, n, p=bp)

    eye = rng.choice(["brown", "blue", "green", "hazel", "grey"], n, p=[0.40, 0.34, 0.1, 0.11, 0.05])
    hair = rng.choice(["brown", "black", "blonde", "red", "auburn"], n, p=[0.44, 0.18, 0.25, 0.05, 0.08])
    greying = (age > 50) & (rng.random(n) < (age - 50) / 35)
    hair[greying] = np.where(rng.random(greying.sum()) < 0.8, "grey", "white")
    hair[(age > 40) & (persons["sex"].to_numpy() == "M") & (rng.random(n) < 0.15)] = "bald"
    persons["eye_colour"], persons["hair_colour"] = eye, hair

    adult = np.where(persons["sex"] == "M", 176, 163) + rng.normal(0, 7, n)
    growth = np.clip(age / 18, 0, 1)
    persons["height_cm"] = np.round(np.where(age < 18, 55 + growth ** 0.8 * (adult - 55), adult - np.maximum(0, age - 70) * 0.15)).astype(np.int16)

    # marital status
    rel_status = np.full(n, "single", dtype=object)
    rel_status[age < 16] = "n/a"
    rel_status[(age >= 60) & (rng.random(n) < 0.25)] = "widowed"
    rel_status[(age >= 35) & (rng.random(n) < 0.10)] = "divorced"
    persons["marital_status"] = rel_status
    return persons


# --------------------------------------------------------------------- economics
def assign_work_and_school(persons: pd.DataFrame, households: pd.DataFrame, places: pd.DataFrame,
                           rng: np.random.Generator) -> pd.DataFrame:
    n = len(persons)
    age = persons["age"].to_numpy()
    r = rng.random(n)
    status = np.full(n, "unemployed", dtype=object)
    status[age < 4] = "child"
    status[(age >= 4) & (age < 18)] = "pupil"
    s1822 = (age >= 18) & (age < 23)
    status[s1822 & (r < 0.45)] = "student"
    status[s1822 & (r >= 0.45) & (r < 0.92)] = "employed"
    work_age = (age >= 23) & (age < 67)
    status[work_age & (r < 0.62)] = "employed"
    status[work_age & (r >= 0.62) & (r < 0.72)] = "self_employed"
    status[work_age & (r >= 0.72) & (r < 0.78)] = "unemployed"
    status[work_age & (r >= 0.78) & (r < 0.90)] = "carer"
    status[work_age & (r >= 0.90) & (r < 0.93)] = "student"
    status[work_age & (r >= 0.93)] = "retired"
    status[(age >= 67)] = np.where(r[age >= 67] < 0.09, "employed", "retired")
    persons["employment_status"] = status

    # education
    edu = np.zeros(n, dtype=np.int8)
    adult = age >= 18
    # district affluence: some neighbourhoods are richer and better-qualified than others
    pdist = households["district_id"].to_numpy()[persons["hh"].to_numpy()]
    affluence = rng.lognormal(0, 0.22, int(pdist.max()) + 1)
    aff = affluence[pdist]
    persons["affluence"] = aff
    e = rng.choice(len(EDU), n, p=[0.16, 0.22, 0.18, 0.10, 0.22, 0.10, 0.02])
    shift = np.clip(np.round(rng.normal(np.log(aff) * 3, 0.6)), -2, 2).astype(int)
    edu[adult] = np.clip(e[adult] + shift[adult], 0, len(EDU) - 1)
    edu[(age >= 16) & (age < 18)] = 1
    edu[status == "student"] = np.maximum(edu[status == "student"], 2)
    persons["education_level"] = np.array(EDU, dtype=object)[edu]

    # occupation by education, income by occupation
    occ = pd.DataFrame(OCCUPATIONS, columns=["title", "sector", "median_salary", "min_edu"])
    occ["occupation_id"] = np.arange(1, len(occ) + 1, dtype=np.int16)
    working = np.isin(status, ["employed", "self_employed"])
    occ_id = np.zeros(n, dtype=np.int16)
    for lvl in range(len(EDU)):
        m = working & (edu == lvl)
        allowed = occ[occ.min_edu <= lvl]
        w = np.where(allowed.min_edu.to_numpy() >= lvl - 2, 3.0, 1.0)  # prefer jobs that use the qualification
        occ_id[m] = rng.choice(allowed.occupation_id.to_numpy(), int(m.sum()), p=w / w.sum())
    persons["occupation_id"] = np.where(occ_id > 0, occ_id, 0)

    med = occ.set_index("occupation_id")["median_salary"]
    income = np.zeros(n)
    wk = occ_id > 0
    exp_factor = np.clip(0.75 + (age - 22) * 0.012, 0.7, 1.25)
    income[wk] = CITY.salary_factor * med.loc[occ_id[wk]].to_numpy() * exp_factor[wk] * aff[wk] ** 0.8 * rng.lognormal(0, 0.28, wk.sum())
    part_time = wk & (rng.random(n) < 0.24)
    income[part_time] *= 0.55
    f = CITY.salary_factor
    income[status == "retired"] = rng.normal(16500 * f, 5500 * f, (status == "retired").sum()).clip(9000 * f)
    income[status == "unemployed"] = rng.normal(5200 * f, 1500 * f, (status == "unemployed").sum()).clip(0)
    income[status == "carer"] = rng.normal(4400 * f, 1200 * f, (status == "carer").sum()).clip(0)
    income[status == "student"] = np.where(rng.random((status == "student").sum()) < 0.35,
                                           rng.normal(7000 * f, 2500 * f, (status == "student").sum()), 0).clip(0)
    persons["annual_income"] = (np.round(income / 100) * 100).astype(np.int32)
    persons["part_time"] = part_time

    # ---------------- workplace (gravity: local pull + city-wide pull by capacity)
    hx = households["x"].to_numpy()[persons["hh"].to_numpy()]
    hy = households["y"].to_numpy()[persons["hh"].to_numpy()]
    persons["hx"], persons["hy"] = hx, hy

    work_places = places[places.category.isin(["workplace", "shop", "food_drink", "healthcare", "leisure",
                                               "school", "university"])].reset_index(drop=True)
    wxy = np.c_[work_places.x, work_places.y]
    # capacity is visitors/pupils for some categories; convert to staff head-count for job weighting
    staff_ratio = work_places.category.map({"school": 0.12, "university": 0.15, "shop": 0.6, "food_drink": 0.7,
                                            "leisure": 0.3, "healthcare": 1.0, "workplace": 1.0}).fillna(1.0)
    wcap = (work_places.capacity * staff_ratio).to_numpy().astype(float)
    wtree = cKDTree(wxy)
    commuters = np.flatnonzero(working & ~((status == "self_employed") & (rng.random(n) < 0.3)))  # some WFH
    wp = np.full(n, -1, dtype=np.int64)
    local = rng.random(len(commuters)) < 0.4
    loc_idx = commuters[local]
    K = 600
    for start in range(0, len(loc_idx), 10_000):
        chunk = loc_idx[start:start + 10_000]
        _, nn = wtree.query(np.c_[hx[chunk], hy[chunk]], k=K)
        w = wcap[nn]
        cw = np.cumsum(w, axis=1)
        pick = (cw < (rng.random(len(chunk)) * cw[:, -1])[:, None]).sum(axis=1)
        wp[chunk] = work_places.place_id.to_numpy()[nn[np.arange(len(chunk)), pick]]
    glob_idx = commuters[~local]
    gp = wcap / wcap.sum()
    wp[glob_idx] = work_places.place_id.to_numpy()[rng.choice(len(work_places), len(glob_idx), p=gp)]
    persons["workplace_id"] = np.where(wp > 0, wp, 0)

    # ---------------- school / university
    schools = places[places.category == "school"].reset_index(drop=True)
    sxy = np.c_[schools.x, schools.y]
    stree = cKDTree(sxy)
    pupils = np.flatnonzero(status == "pupil")
    _, nn = stree.query(np.c_[hx[pupils], hy[pupils]], k=3)
    choice = nn[np.arange(len(pupils)), rng.choice(3, len(pupils), p=[0.6, 0.25, 0.15])]
    sc = np.zeros(n, dtype=np.int64)
    sc[pupils] = schools.place_id.to_numpy()[choice]
    unis = places[places.category == "university"].reset_index(drop=True)
    studs = np.flatnonzero(status == "student")
    if len(unis):
        up = unis.capacity.to_numpy() / unis.capacity.sum()
        sc[studs] = unis.place_id.to_numpy()[rng.choice(len(unis), len(studs), p=up)]
    persons["school_id"] = sc

    # ---------------- commute mode
    place_xy = places.set_index("place_id")[["x", "y"]]
    dest = np.where(persons["workplace_id"] > 0, persons["workplace_id"], persons["school_id"])
    has_dest = dest > 0
    dx = np.zeros(n); dy = np.zeros(n)
    dx[has_dest] = place_xy.loc[dest[has_dest], "x"].to_numpy()
    dy[has_dest] = place_xy.loc[dest[has_dest], "y"].to_numpy()
    dist = np.hypot(dx - hx, dy - hy)
    hh_car = households["has_car"].to_numpy()[persons["hh"].to_numpy()]
    mode = np.full(n, "none", dtype=object)
    mc = CITY.modes
    walkable = dist < mc.walk_max_m
    far = has_dest & ~walkable
    can_drive = hh_car & (age >= 17)
    for mask, probs in [(has_dest & walkable, mc.short), (far & can_drive, mc.far_with_car),
                        (far & ~can_drive, mc.far_without_car)]:
        mode[mask] = _pick(probs, int(mask.sum()), rng)
    mode[has_dest & walkable & ~can_drive & (mode == "car")] = "walk"
    young_kid_far = (status == "pupil") & (age < 11) & ~walkable
    mode[young_kid_far & hh_car] = "car"
    persons["commute_mode"] = mode
    return persons, occ


# --------------------------------------------------------------------- housing
def house_households(households: pd.DataFrame, persons: pd.DataFrame, buildings: pd.DataFrame,
                     street: np.ndarray, house_no: np.ndarray, districts: pd.DataFrame,
                     rng: np.random.Generator) -> pd.DataFrame:
    """Place every household into a residential building, then derive address fields.

    Each residential building holds floor_area / sqm_per_dwelling homes (at least one), so a
    detached house takes one household and a canal-side block takes several. If OSM has too
    few homes mapped, every building's capacity is scaled up proportionally.
    """
    H = len(households)
    size = persons.groupby("hh").size().reindex(range(H), fill_value=0).to_numpy()
    res = buildings[buildings.use_class.isin(["residential", "apartments"])]
    cap = np.maximum(1, np.round(res.footprint_m2 * res.levels / CITY.sqm_per_dwelling)).astype(int).to_numpy()
    if cap.sum() < H * 1.05:
        cap = np.ceil(cap * (H * 1.05 / cap.sum())).astype(int)
    slot_building = np.repeat(res.index.to_numpy(), cap)
    slot_cap = np.repeat(cap, cap)
    chosen = rng.choice(len(slot_building), H, replace=False)
    # bigger families get the slots in small buildings (houses); singles and sharers end up in blocks of flats
    chosen = chosen[np.argsort(slot_cap[chosen] + rng.normal(0, 1.5, H), kind="stable")]
    pref = size + rng.normal(0, 1.2, H) + np.where(households["type"] == "shared", -1.5, 0)
    order = np.argsort(-pref)
    bidx = np.empty(H, dtype=np.int64)
    bidx[order] = slot_building[chosen]

    b = buildings.loc[bidx]
    households["building_id"] = b["building_id"].to_numpy()
    households["district_id"] = b["district_id"].to_numpy()
    households["street"] = street[bidx]
    households["house_number"] = house_no[bidx]
    per_building = pd.Series(bidx).groupby(bidx).cumcount().to_numpy() + 1
    multi = pd.Series(bidx).map(pd.Series(bidx).value_counts()).to_numpy() > 1
    households["flat_number"] = np.where(multi, per_building, 0)

    jitter = rng.normal(0, 2.0, (H, 2))
    households["x"] = b["x"].to_numpy() + jitter[:, 0]
    households["y"] = b["y"].to_numpy() + jitter[:, 1]

    h1 = pd.util.hash_array(households["street"].astype(str).to_numpy().astype(object))
    h2 = pd.util.hash_array((households["street"].astype(str) + (households["house_number"] // 24).astype(str))
                            .to_numpy().astype(object))
    rank = households["district_id"].to_numpy().astype(int) - 1
    households["postcode"] = CITY.postcode(rank, h1, h2, len(districts))

    centre = np.array([buildings.x.median(), buildings.y.median()])
    dc_km = np.hypot(households["x"] - centre[0], households["y"] - centre[1]) / 1000
    mc = CITY.modes
    car_p = np.clip(mc.car_ownership_base + dc_km * mc.car_ownership_per_km, mc.car_ownership_base, mc.car_ownership_max)
    households["has_car"] = rng.random(H) < car_p
    tenure_p = np.where(households["type"].isin(["shared", "single"]), 0.35, 0.55)
    u = rng.random(H)
    households["tenure"] = np.where(u < tenure_p, "owner_occupied",
                                    np.where(u < tenure_p + 0.25, "private_rent", "social_rent"))
    return households
