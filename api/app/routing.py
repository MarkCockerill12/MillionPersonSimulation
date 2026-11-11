"""In-memory shortest-path routing over the road graph stored in PostGIS.

The graph (road_nodes / road_edges) is read once at startup into a SciPy sparse matrix, so a
route is a C-speed Dijkstra plus a geometry stitch: typically a few milliseconds, versus a few
hundred for building the graph inside pgr_dijkstra on every request. The same query is still
available in SQL; see the pgRouting example in the SQL console.
"""

from __future__ import annotations

import itertools
import threading
import time

import numpy as np
import shapely
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import dijkstra
from scipy.spatial import cKDTree

from .db import cursor


class Router:
    def __init__(self) -> None:
        t = time.perf_counter()
        with cursor(dict_rows=False) as cur:
            cur.execute("SELECT node_id, ST_X(geom), ST_Y(geom) FROM road_nodes ORDER BY node_id")
            nodes = np.array(cur.fetchall(), dtype=np.float64)
            cur.execute("SELECT source, target, cost_s, length_m, ST_AsBinary(geom), highway FROM road_edges")
            edges = cur.fetchall()
        self.node_ids = nodes[:, 0].astype(np.int64)
        self.lon, self.lat = nodes[:, 1], nodes[:, 2]
        self.kx = np.cos(np.radians(self.lat.mean()))
        self.tree = cKDTree(np.c_[self.lon * self.kx, self.lat])

        src = np.searchsorted(self.node_ids, np.array([e[0] for e in edges], dtype=np.int64))
        dst = np.searchsorted(self.node_ids, np.array([e[1] for e in edges], dtype=np.int64))
        cost = np.array([e[2] for e in edges], dtype=np.float64)
        self.length = np.array([e[3] for e in edges], dtype=np.float64)
        self.geoms = shapely.from_wkb([bytes(e[4]) for e in edges])

        highway = np.array([e[5] or "" for e in edges], dtype=object)
        n = len(self.node_ids)

        def build(weight: np.ndarray, allowed: np.ndarray) -> tuple[csr_matrix, dict[tuple[int, int], int]]:
            # cheapest allowed edge per (u, v): parallel edges exist in OSM multigraphs
            idx = np.flatnonzero(allowed)
            order = idx[np.lexsort((weight[idx], dst[idx], src[idx]))]
            first = np.r_[True, (np.diff(src[order]) != 0) | (np.diff(dst[order]) != 0)]
            keep = order[first]
            graph = csr_matrix((weight[keep], (src[keep], dst[keep])), shape=(n, n))
            return graph, {(int(u), int(v)): int(i) for u, v, i in zip(src[keep], dst[keep], keep, strict=True)}

        # cars: fastest route on every road. Walking and cycling: shortest route, keeping off motorways and trunk roads.
        no_fast_roads = ~np.isin(highway, ["motorway", "motorway_link", "trunk", "trunk_link"])
        self.graphs = {"car": build(cost, np.ones(len(edges), bool)), "active": build(self.length, no_fast_roads)}
        self.load_ms = round((time.perf_counter() - t) * 1000)

    def nearest(self, lon: float, lat: float) -> int:
        return int(self.tree.query([lon * self.kx, lat])[1])

    def route(self, a: int, b: int, profile: str = "car") -> tuple[list[list[float]], float, float] | None:
        """Best path between node indexes → (coords, metres, cost). profile: "car" or "active" (walk/cycle)."""
        if a == b:
            return None
        graph, edge_of = self.graphs[profile]
        dist, pred = dijkstra(graph, directed=True, indices=a, return_predecessors=True)
        if not np.isfinite(dist[b]):
            return self.route(a, b, "car") if profile != "car" else None
        path = [b]
        while path[-1] != a:
            path.append(int(pred[path[-1]]))
        path.reverse()
        coords: list[list[float]] = []
        metres = 0.0
        for u, v in itertools.pairwise(path):
            i = edge_of[(u, v)]
            seg = shapely.get_coordinates(self.geoms[i]).round(6).tolist()
            coords.extend(seg if not coords else seg[1:])
            metres += self.length[i]
        return coords, metres, float(dist[b])


_router: Router | None = None
_lock = threading.Lock()


def get_router() -> Router:
    global _router
    with _lock:
        if _router is None:
            _router = Router()
        return _router


def reset_router() -> None:
    global _router
    with _lock:
        _router = None
