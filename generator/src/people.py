"""Synthetic population: households, people, family links, jobs and schools.

Distributions are loosely calibrated to ONS Census 2021 figures for Birmingham
(household size mix, age structure, employment rate, commute mode share).
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass

import numpy as np
import pandas as pd
from faker import Faker
from scipy.spatial import cKDTree

REF_DATE = np.datetime64("2026-01-01")

# Name locales (all Latin script) and their population share.
LOCALES = {
    "en_GB": 0.62, "en_IN": 0.13, "en_IE": 0.04, "pl_PL": 0.04, "yo_NG": 0.03, "en_US": 0.04,
    "it_IT": 0.02, "ro_RO": 0.02, "fr_FR": 0.02, "es_ES": 0.02, "pt_PT": 0.02,
}

HOUSEHOLD_TYPES = {  # type -> probability
    "single": 0.29, "couple": 0.23, "couple_with_children": 0.23, "single_parent": 0.10,
    "multigenerational": 0.07, "shared": 0.08,
}

COLOURS = {"blue": 0.24, "green": 0.13, "purple": 0.11, "red": 0.09, "black": 0.08, "teal": 0.05,
           "orange": 0.05, "pink": 0.05, "yellow": 0.04, "grey": 0.03, "white": 0.03, "navy": 0.03,
           "turquoise": 0.02, "gold": 0.02, "maroon": 0.02, "lilac": 0.01}
BLOOD = {"O+": 0.35, "A+": 0.30, "B+": 0.08, "AB+": 0.02, "O-": 0.13, "A-": 0.08, "B-": 0.02, "AB-": 0.02}
EDU = ["none", "gcse", "a_level", "apprenticeship", "bachelors", "masters", "doctorate"]

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
    ("Solicitor", "Legal", 58000, 4), ("Pharmacist", "Health & Social Care", 47500, 5),
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
def compose_households(n_people: int, rng: np.random.Generator):
    """Build household membership + intra-household relationships (no names yet)."""
    types, tp = _weighted(HOUSEHOLD_TYPES)
    locs, lp = _weighted(LOCALES)

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
        return "F" if rng.random() < 0.51 else "M"

    def couple(h: int, loc: int, age: int) -> tuple[int, int]:
        nonlocal fam
        s1 = sex()
        same = rng.random() < 0.03
        s2 = s1 if same else ("M" if s1 == "F" else "F")
        a2 = int(np.clip(age + rng.normal(0, 3.5), 18, 99))
        fam += 1
        f1 = fam
        married = rng.random() < 0.65
        if married and rng.random() < 0.8:
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
        kids = []
        first_gap = rng.integers(20, 36)
        for i in range(n):
            age = youngest_parent - first_gap - i * rng.integers(1, 4)
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
        t = types[rng.choice(len(types), p=tp)]
        loc = int(rng.choice(len(locs), p=lp))
        if t == "single":
            age = rng.gamma(4.5, 11) + 18
            age = int(age if age <= 96 else rng.uniform(60, 92))  # resample the tail instead of piling it at the cap
            fam += 1
            add(h, "head", age, sex(), fam, loc)
        elif t == "couple":
            age = int(np.clip(rng.choice([rng.normal(30, 6), rng.normal(64, 10)]), 19, 98))
            couple(h, loc, age)
        elif t == "couple_with_children":
            age = int(np.clip(rng.normal(41, 8), 22, 70))
            a, b = couple(h, loc, age)
            n = rng.choice([1, 2, 3, 4, 5], p=[0.38, 0.40, 0.15, 0.05, 0.02])
            if not children(h, loc, [a, b], n, p_family[a]):
                t = "couple"
        elif t == "single_parent":
            fam += 1
            age = int(np.clip(rng.normal(38, 8), 19, 65))
            s = "F" if rng.random() < 0.86 else "M"
            a = add(h, "head", age, s, fam, loc)
            children(h, loc, [a], rng.choice([1, 2, 3], p=[0.5, 0.35, 0.15]), fam)
        elif t == "multigenerational":
            age = int(np.clip(rng.normal(42, 7), 26, 60))
            a, b = couple(h, loc, age)
            kids = children(h, loc, [a, b], rng.choice([1, 2, 3], p=[0.35, 0.45, 0.2]), p_family[a])
            nga = rng.choice([1, 2], p=[0.55, 0.45])
            gage = int(min(100, p_age[a] + rng.integers(24, 34)))
            g1 = add(h, "parent", gage, sex(), p_family[a], loc)
            gps = [g1]
            if nga == 2:
                g2 = add(h, "parent", min(100, gage + int(rng.integers(-4, 5))),
                         "M" if p_sex[g1] == "F" else "F", p_family[a], loc)
                link(g1, g2, "spouse", "spouse")
                gps.append(g2)
            for g in gps:
                link(g, a, "parent", "child")
                for k in kids:
                    link(g, k, "grandparent", "grandchild")
        else:  # shared house of unrelated adults
            n = rng.choice([2, 3, 4, 5], p=[0.35, 0.35, 0.2, 0.1])
            for i in range(n):
                fam += 1
                add(h, "head" if i == 0 else "housemate", int(np.clip(rng.normal(25, 4), 18, 45)), sex(), fam,
                    int(rng.choice(len(locs), p=lp)))
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
    family shares a surname across two addresses.
    """
    heads = persons[persons.role == "head"]
    young = heads[heads.age.between(20, 40)]
    young = young[rng.random(len(young)) < 0.35]
    old_heads = persons[(persons.role == "head") & persons.age.between(48, 85)]
    by_loc = {loc: g.index.to_numpy() for loc, g in old_heads.groupby("loc")}
    hh_members = persons.groupby("hh").groups
    used: set[int] = set()
    remap: dict[int, int] = {}
    pairs: list[tuple[int, int, str]] = []
    for idx, row in young.iterrows():
        cand = by_loc.get(row["loc"])
        if cand is None:
            continue
        ph = int(cand[rng.integers(len(cand))])
        if ph in used or persons.at[ph, "age"] - row["age"] < 20:
            continue
        used.add(ph)
        remap[row["family"]] = persons.at[ph, "family"]
        for m in hh_members[persons.at[ph, "hh"]]:
            role = persons.at[m, "role"]
            if role in ("head", "partner"):
                pairs += [(m, idx, "parent"), (idx, m, "child")]
            elif role == "child":
                pairs += [(m, idx, "sibling"), (idx, m, "sibling")]
    persons["family"] = persons["family"].replace(remap)
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
    persons["citizen_id"] = ["BHM-" + "".join(c[:4]) + "-" + "".join(c[4:]) for c in chars]
    colours = rng.choice(256 ** 3, n, replace=False)
    persons["signature_colour"] = [f"#{c:06X}" for c in colours]

    domains = np.array(["mailbox.example", "brum.example", "postbox.example", "inbox.example", "letter.example"])
    base = [f"{_ascii(f)}.{_ascii(l)}" for f, l in zip(persons["first_name"], persons["last_name"])]
    base = pd.Series(base)
    suffix = base.groupby(base).cumcount()
    persons["email"] = (base + np.where(suffix > 0, suffix.astype(str), "") + "@" +
                        rng.choice(domains, n)).to_numpy()
    persons["phone"] = ["07700 900" + f"{x:03d}" for x in rng.integers(0, 1000, n)]  # Ofcom drama range

    age = persons["age"].to_numpy()
    days = rng.integers(0, 365, n)
    persons["date_of_birth"] = (REF_DATE - (age * 365.25).astype("timedelta64[D]") - days.astype("timedelta64[D]")).astype("datetime64[D]")

    ck, cp = _weighted(COLOURS)
    persons["favourite_colour"] = rng.choice(ck, n, p=cp)
    bk, bp = _weighted(BLOOD)
    persons["blood_type"] = rng.choice(bk, n, p=bp)

    south_asian_or_african = persons["loc"].isin([locs.index("en_IN"), locs.index("yo_NG")]).to_numpy()
    eye = rng.choice(["brown", "blue", "green", "hazel", "grey"], n, p=[0.42, 0.32, 0.1, 0.11, 0.05])
    eye[south_asian_or_african] = rng.choice(["brown", "hazel"], south_asian_or_african.sum(), p=[0.93, 0.07])
    hair = rng.choice(["brown", "black", "blonde", "red", "auburn"], n, p=[0.45, 0.2, 0.22, 0.05, 0.08])
    hair[south_asian_or_african] = rng.choice(["black", "brown"], south_asian_or_african.sum(), p=[0.85, 0.15])
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
    income[wk] = med.loc[occ_id[wk]].to_numpy() * exp_factor[wk] * aff[wk] ** 0.8 * rng.lognormal(0, 0.28, wk.sum())
    part_time = wk & (rng.random(n) < 0.24)
    income[part_time] *= 0.55
    income[status == "retired"] = rng.normal(16500, 5500, (status == "retired").sum()).clip(9000)
    income[status == "unemployed"] = rng.normal(5200, 1500, (status == "unemployed").sum()).clip(0)
    income[status == "carer"] = rng.normal(4400, 1200, (status == "carer").sum()).clip(0)
    income[status == "student"] = np.where(rng.random((status == "student").sum()) < 0.35,
                                           rng.normal(7000, 2500, (status == "student").sum()), 0).clip(0)
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
    u = rng.random(n)
    walkable = dist < 1200
    mode[has_dest & walkable] = np.where(u[has_dest & walkable] < 0.55, "walk",
                                         np.where(u[has_dest & walkable] < 0.60, "cycle", "car"))
    far = has_dest & ~walkable
    can_drive = hh_car & (age >= 17)
    mode[far & can_drive] = np.where(u[far & can_drive] < 0.72, "car",
                                     np.where(u[far & can_drive] < 0.88, "bus",
                                              np.where(u[far & can_drive] < 0.97, "train", "cycle")))
    mode[far & ~can_drive] = np.where(u[far & ~can_drive] < 0.72, "bus",
                                      np.where(u[far & ~can_drive] < 0.86, "train",
                                               np.where(u[far & ~can_drive] < 0.92, "cycle", "walk")))
    young_kid_far = (status == "pupil") & (age < 11) & ~walkable
    mode[young_kid_far & hh_car] = "car"
    persons["commute_mode"] = mode
    return persons, occ


# --------------------------------------------------------------------- housing
def house_households(households: pd.DataFrame, persons: pd.DataFrame, buildings: pd.DataFrame,
                     street: np.ndarray, house_no: np.ndarray, districts: pd.DataFrame,
                     rng: np.random.Generator) -> pd.DataFrame:
    """Place every household into a residential building, then derive address fields."""
    H = len(households)
    size = persons.groupby("hh").size().reindex(range(H), fill_value=0).to_numpy()
    res = buildings[buildings.use_class.isin(["residential", "apartments"])]
    houses = res[res.use_class == "residential"].index.to_numpy().copy()
    flats = res[res.use_class == "apartments"]
    flat_cap = np.maximum(2, (flats.footprint_m2 * flats.levels / 75).round().astype(int)).to_numpy()
    flat_slots = np.repeat(flats.index.to_numpy(), flat_cap)
    rng.shuffle(houses)
    rng.shuffle(flat_slots)

    # bigger families prefer houses; singles/sharers prefer flats
    pref = size + rng.normal(0, 1.2, H) + np.where(households["type"] == "shared", -1.5, 0)
    order = np.argsort(-pref)
    n_house = min(len(houses), int(H * 0.72))
    n_flat = H - n_house
    if n_flat > len(flat_slots):  # not enough flats: houses split into maisonettes / HMOs
        extra = rng.choice(houses, n_flat - len(flat_slots))
        flat_slots = np.r_[flat_slots, extra]
    slots = np.r_[houses[:n_house], flat_slots[:n_flat]]
    bidx = np.empty(H, dtype=np.int64)
    bidx[order] = slots

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

    pc_district = districts.set_index("district_id")["postcode_district"]
    letters = np.array(list("ABDEFGHJLNPQRSTUWXYZ"))
    h1 = pd.util.hash_array(households["street"].astype(str).to_numpy().astype(object))
    h2 = pd.util.hash_array((households["street"].astype(str) + (households["house_number"] // 24).astype(str)).to_numpy().astype(object))
    households["postcode"] = (pc_district.loc[households["district_id"]].to_numpy() + " " +
                              (h1 % 9 + 1).astype(str) + letters[h2 % 20] + letters[(h2 // 20) % 20])

    centre = np.array([buildings.x.median(), buildings.y.median()])
    dc = np.hypot(households["x"] - centre[0], households["y"] - centre[1])
    car_p = np.clip(0.35 + dc / 12000, 0.35, 0.85)
    households["has_car"] = rng.random(H) < car_p
    tenure_p = np.where(households["type"].isin(["shared", "single"]), 0.4, 0.62)
    u = rng.random(H)
    households["tenure"] = np.where(u < tenure_p, "owner_occupied", np.where(u < tenure_p + 0.2, "private_rent", "social_rent"))
    return households
