"""Daily activity schedules (one typical weekday per person), fully vectorised.

Pattern:  home -> [stop 1] -> [stop 2] -> home
  workers   stop1 = workplace, stop2 = optional after-work errand
  pupils    stop1 = school,    stop2 = optional after-school leisure
  students  stop1 = university,stop2 = optional evening out
  others    stop1 = errand,    stop2 = optional second errand
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.spatial import cKDTree

CIRCUITY = 1.35
SPEED_M_PER_MIN = {"walk": 80, "cycle": 250, "bus": 230, "train": 520, "car": 420}
OVERHEAD_MIN = {"walk": 0, "cycle": 2, "bus": 9, "train": 15, "car": 4}
ERRANDS = ["shopping", "food_drink", "leisure", "healthcare"]
ERRAND_CATEGORY = {"shopping": "shop", "food_drink": "food_drink", "leisure": "leisure", "healthcare": "healthcare"}


def travel_minutes(dist_m: np.ndarray, mode: np.ndarray) -> np.ndarray:
    speed = pd.Series(mode).map(SPEED_M_PER_MIN).fillna(80).to_numpy()
    over = pd.Series(mode).map(OVERHEAD_MIN).fillna(0).to_numpy()
    return np.maximum(2, np.round(dist_m * CIRCUITY / speed + over)).astype(np.int32)


def _pick_near(places: pd.DataFrame, category: str, ax: np.ndarray, ay: np.ndarray, rng: np.random.Generator,
               k: int = 25) -> np.ndarray:
    pl = places[places.category == category].reset_index(drop=True)
    if len(ax) == 0 or len(pl) == 0:
        return np.zeros(len(ax), dtype=np.int64)
    k = min(k, len(pl))
    tree = cKDTree(np.c_[pl.x, pl.y])
    out = np.empty(len(ax), dtype=np.int64)
    cap = pl.capacity.to_numpy().astype(float)
    ids = pl.place_id.to_numpy()
    for s in range(0, len(ax), 100_000):
        _, nn = tree.query(np.c_[ax[s:s + 100_000], ay[s:s + 100_000]], k=k)
        nn = nn.reshape(len(nn), -1)
        w = cap[nn] * np.linspace(1.0, 0.3, nn.shape[1])  # closer = likelier
        cw = np.cumsum(w, axis=1)
        pick = (cw < (rng.random(len(nn)) * cw[:, -1])[:, None]).sum(axis=1)
        out[s:s + len(nn)] = ids[nn[np.arange(len(nn)), np.minimum(pick, nn.shape[1] - 1)]]
    return out


def build_activities(persons: pd.DataFrame, places: pd.DataFrame, households: pd.DataFrame,
                     rng: np.random.Generator) -> pd.DataFrame:
    n = len(persons)
    status = persons["employment_status"].to_numpy()
    age = persons["age"].to_numpy()
    hx, hy = persons["hx"].to_numpy(), persons["hy"].to_numpy()
    pxy = places.set_index("place_id")[["x", "y"]]

    work = persons["workplace_id"].to_numpy()
    school = persons["school_id"].to_numpy()
    primary = np.where(work > 0, work, school)
    has_primary = primary > 0
    ptype = np.where(work > 0, "work", np.where(status == "pupil", "school", "university")).astype(object)

    # ---- stop 1
    s1_valid = has_primary.copy()
    s1_place = primary.astype(np.int64)
    s1_type = ptype.copy()
    s1_start = np.zeros(n, dtype=np.int32)
    s1_dur = np.zeros(n, dtype=np.int32)

    w = (work > 0)
    shift = rng.random(n)
    start_w = np.where(shift < 0.80, rng.normal(510, 40, n), np.where(shift < 0.92, rng.normal(390, 30, n), rng.normal(840, 30, n)))
    dur_w = np.where(persons["part_time"].to_numpy(), rng.normal(270, 40, n), rng.normal(510, 30, n))
    s1_start[w], s1_dur[w] = start_w[w], dur_w[w]
    pu = status == "pupil"
    s1_start[pu & has_primary] = rng.normal(525, 8, (pu & has_primary).sum())
    s1_dur[pu & has_primary] = rng.normal(390, 10, (pu & has_primary).sum())
    st = (status == "student") & has_primary
    s1_start[st] = rng.normal(600, 60, st.sum())
    s1_dur[st] = rng.normal(330, 70, st.sum()).clip(90)

    # non-commuters: first errand of the day
    goes_out_p = np.select([status == "retired", np.isin(status, ["unemployed", "carer", "self_employed", "employed"]),
                            status == "child"], [0.65, 0.6, 0.0], 0.5)
    out = ~has_primary & (rng.random(n) < goes_out_p) & (age >= 4)
    e1_cat = rng.choice(ERRANDS, n, p=[0.5, 0.18, 0.22, 0.10])
    s1_valid |= out
    s1_type[out] = e1_cat[out]
    s1_start[out] = rng.normal(630, 100, out.sum()).clip(450, 1080)
    s1_dur[out] = rng.integers(20, 140, out.sum())

    # ---- stop 2 (optional)
    p2 = np.select([w, pu, st, out], [0.33, np.where(age >= 8, 0.25, 0.0), 0.4, 0.35], 0.0)
    s2_valid = s1_valid & (rng.random(n) < p2)
    s2_cat = np.where(pu | st, rng.choice(["leisure", "food_drink"], n, p=[0.7, 0.3]),
                      rng.choice(ERRANDS, n, p=[0.45, 0.30, 0.20, 0.05])).astype(object)
    s2_type = np.where(s2_valid, s2_cat, None)
    s2_gap = rng.integers(0, 60, n)
    s2_dur = rng.integers(20, 120, n)

    # ---- choose errand places
    s1x = np.zeros(n); s1y = np.zeros(n)
    s1x[has_primary] = pxy.loc[s1_place[has_primary], "x"].to_numpy()
    s1y[has_primary] = pxy.loc[s1_place[has_primary], "y"].to_numpy()
    for cat in ERRANDS:
        m = out & (s1_type == cat)
        s1_place[m] = _pick_near(places, ERRAND_CATEGORY[cat], hx[m], hy[m], rng)
    s1_valid &= s1_place > 0
    m = out & s1_valid
    s1x[m] = pxy.loc[s1_place[m], "x"].to_numpy()
    s1y[m] = pxy.loc[s1_place[m], "y"].to_numpy()

    near_home = rng.random(n) < 0.5
    ax, ay = np.where(near_home, hx, s1x), np.where(near_home, hy, s1y)
    s2_place = np.zeros(n, dtype=np.int64)
    for cat in ERRANDS:
        mm = s2_valid & (s2_type == cat)
        s2_place[mm] = _pick_near(places, ERRAND_CATEGORY[cat], ax[mm], ay[mm], rng)
    s2_valid &= s2_place > 0
    s2x = np.zeros(n); s2y = np.zeros(n)
    s2x[s2_valid] = pxy.loc[s2_place[s2_valid], "x"].to_numpy()
    s2y[s2_valid] = pxy.loc[s2_place[s2_valid], "y"].to_numpy()

    # ---- travel times
    mode = persons["commute_mode"].to_numpy().copy()
    hh_car = households["has_car"].to_numpy()[persons["hh"].to_numpy()]
    d_h1 = np.hypot(s1x - hx, s1y - hy)
    no_mode = mode == "none"
    mode[no_mode] = np.where(d_h1[no_mode] < 1500, "walk", np.where(hh_car[no_mode] & (age[no_mode] >= 17), "car", "bus"))
    persons["commute_mode"] = np.where(s1_valid, mode, "none")

    t_h1 = travel_minutes(d_h1, mode)
    t_12 = travel_minutes(np.hypot(s2x - s1x, s2y - s1y), mode)
    last_x, last_y = np.where(s2_valid, s2x, s1x), np.where(s2_valid, s2y, s1y)
    t_lh = travel_minutes(np.hypot(hx - last_x, hy - last_y), mode)

    s1_start = np.clip(s1_start, t_h1 + 1, 1300)
    s1_end = np.minimum(s1_start + np.maximum(s1_dur, 10), 1380)
    home_end = s1_start - t_h1
    s2_start = s1_end + t_12 + s2_gap
    s2_end = np.minimum(s2_start + s2_dur, 1420)
    s2_valid &= s2_start < 1400
    last_end = np.where(s2_valid, s2_end, s1_end)
    home_start = np.minimum(last_end + t_lh, 1439)

    # ---- emit rows
    pid = persons["person_id"].to_numpy()
    stay = ~s1_valid
    frames = [
        pd.DataFrame({"person_id": pid[stay], "seq": 0, "activity_type": "home", "place_id": 0,
                      "start_min": 0, "end_min": 1439}),
    ]
    v = s1_valid
    frames.append(pd.DataFrame({"person_id": pid[v], "seq": 0, "activity_type": "home", "place_id": 0,
                                "start_min": 0, "end_min": home_end[v]}))
    frames.append(pd.DataFrame({"person_id": pid[v], "seq": 1, "activity_type": s1_type[v], "place_id": s1_place[v],
                                "start_min": s1_start[v], "end_min": s1_end[v]}))
    v2 = s2_valid
    frames.append(pd.DataFrame({"person_id": pid[v2], "seq": 2, "activity_type": s2_type[v2], "place_id": s2_place[v2],
                                "start_min": s2_start[v2], "end_min": s2_end[v2]}))
    frames.append(pd.DataFrame({"person_id": pid[v], "seq": np.where(v2[v], 3, 2), "activity_type": "home",
                                "place_id": 0, "start_min": home_start[v], "end_min": 1439}))
    acts = pd.concat(frames, ignore_index=True)
    acts["start_min"] = acts["start_min"].clip(0, 1439)
    acts["end_min"] = np.maximum(acts["end_min"].clip(0, 1439), acts["start_min"])
    return acts.sort_values(["person_id", "seq"]).reset_index(drop=True)
