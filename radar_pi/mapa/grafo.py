"""Malha viária OSM como grafo dirigido, em metros (UTM 23S / EPSG:31983)."""
from __future__ import annotations

import math
import re
import numpy as np
from pyproj import Transformer
from shapely import STRtree
from shapely.geometry import LineString, Point

from radar_pi.coletor.util import sem_acento

_FWD = Transformer.from_crs("EPSG:4326", "EPSG:31983", always_xy=True)
_INV = Transformer.from_crs("EPSG:31983", "EPSG:4326", always_xy=True)

_ABREV = {"AVENIDA": "AV", "RUA": "R", "ALAMEDA": "AL", "ESTRADA": "ESTR", "PRACA": "PCA",
          "VIADUTO": "VD", "PONTE": "PTE", "TUNEL": "TUN", "RODOVIA": "ROD", "MARGINAL": "MARG",
          "DOUTOR": "DR", "PROFESSOR": "PROF", "PRESIDENTE": "PRES", "GOVERNADOR": "GOV",
          "ENGENHEIRO": "ENG", "DEPUTADO": "DEP", "SENADOR": "SEN", "BRIGADEIRO": "BRIG",
          "COMENDADOR": "COM", "MARECHAL": "MAL", "GENERAL": "GAL", "CORONEL": "CEL", "SANTO": "STO",
          "SANTA": "STA", "SAO": "S", "NOSSA SENHORA": "N SRA"}


def norm_nome(s) -> str:
    if s is None or (isinstance(s, float) and math.isnan(s)):
        return ""
    if isinstance(s, list):
        s = s[0]
    s = sem_acento(str(s))
    s = re.sub(r"[^A-Z0-9 ]", " ", s)
    for k, v in _ABREV.items():
        s = re.sub(rf"\b{k}\b", v, s)
    s = re.sub(r"\b(DE|DA|DO|DOS|DAS|E)\b", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def to_xy(lat, lon):
    x, y = _FWD.transform(lon, lat)
    return x, y


def to_latlon(x, y):
    lon, lat = _INV.transform(x, y)
    return lat, lon


def _maxspeed(v):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    if isinstance(v, list):
        v = v[0]
    m = re.search(r"\d+", str(v))
    return int(m.group()) if m else None


class Adj:
    """Adjacência dirigida enxuta (substitui o networkx: menos memória). Mantém, entre u->v, a aresta mais curta."""

    def __init__(self):
        self.succ, self.pred = {}, {}

    def add_edge(self, u, v, w, e):
        d = self.succ.setdefault(u, {})
        if v not in d or d[v]["w"] > w:
            d[v] = {"w": w, "e": e}
        self.pred.setdefault(v, set()).add(u)

    def remove_edge_id(self, u, v, e):
        d = self.succ.get(u)
        if d and v in d and d[v]["e"] == e:
            del d[v]
            if not d:
                del self.succ[u]
            ps = self.pred.get(v)
            if ps:
                ps.discard(u)

    def has_edge(self, u, v):
        return v in self.succ.get(u, ())

    def __getitem__(self, u):
        return self.succ[u]

    def predecessors(self, n):
        return list(self.pred.get(n, ()))


def dijkstra(adj: Adj, src, cutoff):
    import heapq
    dist = {src: 0.0}
    h = [(0.0, src)]
    while h:
        d, n = heapq.heappop(h)
        if d > dist.get(n, math.inf):
            continue
        for v, a in adj.succ.get(n, {}).items():
            nd = d + a["w"]
            if nd <= cutoff and nd < dist.get(v, math.inf):
                dist[v] = nd
                heapq.heappush(h, (nd, v))
    return dist


def _attr_de(d, comprimento):
    ref = d.get("ref")
    hw = d.get("highway")
    return {"nome": norm_nome(d.get("name")), "nome_orig": d.get("name"),
            "ref": ref[0] if isinstance(ref, list) else ref,
            "highway": hw[0] if isinstance(hw, list) else hw,
            "maxspeed": _maxspeed(d.get("maxspeed")), "oneway": bool(d.get("oneway")), "len": comprimento}


class MalhaOps:
    """Operações comuns (map matching, alerta, rotas) sobre edges/geoms/attr/R/out_edges."""

    def garantir(self, x, y):
        """Carrega a região em volta de (x,y) se a malha for por tiles; aqui no-op."""

    def candidatos(self, x, y, raio=50.0, max_n=12):
        self.garantir(x, y)
        p = Point(x, y)
        out = []
        for i in self._consulta(p.buffer(raio)):
            g = self.geoms[i]
            d = g.distance(p)
            if d <= raio:
                out.append((int(i), d, g.project(p)))
        out.sort(key=lambda t: t[1])
        return out[:max_n]

    def rumo(self, i, offset):
        """Rumo (graus, 0=N, horário) da aresta i na posição offset."""
        g = self.geoms[i]
        a = g.interpolate(max(0.0, offset - 3))
        b = g.interpolate(min(g.length, offset + 3))
        return (math.degrees(math.atan2(b.x - a.x, b.y - a.y)) + 360) % 360

    def ponto(self, i, offset):
        p = self.geoms[i].interpolate(offset)
        return p.x, p.y

    def _dists_de(self, no, cutoff):
        k = (no, cutoff)
        c = self._cache_dij
        if k not in c:
            if len(c) > 4096:
                c.clear()
            c[k] = dijkstra(self.R, no, cutoff)
        return c[k]

    def dist_rota(self, i1, off1, i2, off2, cutoff=2000.0):
        """Distância ao longo da via de (aresta i1, off1) até (aresta i2, off2). inf se não alcança."""
        if i1 == i2 and off2 >= off1 - 1.0:
            return max(0.0, off2 - off1)
        resto = self.attr[i1]["len"] - off1
        v = self.edges[i1][1]
        u2 = self.edges[i2][0]
        c = int(math.ceil(cutoff / 100.0) * 100)
        d = self._dists_de(v, c).get(u2)
        if d is None:
            return math.inf
        return resto + d + off2

    def continuacao_mesma_via(self, i, permitir_troca_nome=True):
        """Próxima aresta da MESMA via (mesmo nome/ref), a mais reta. E6: se nenhuma tem o mesmo nome,
        aceita a continuação quase reta (<=20°) de mesma classe (a via só mudou de nome)."""
        u, v, _ = self.edges[i]
        if v not in self.out_edges:
            self.garantir(*self.node_xy_de(v))
        a = self.attr[i]
        rumo_fim = self.rumo(i, a["len"])
        melhor, melhor_ang, reto, reto_ang = None, 999, None, 999
        for j in self.out_edges.get(v, []):
            if self.edges[j][1] == u:
                continue  # retorno
            b = self.attr[j]
            ang = abs((self.rumo(j, 0) - rumo_fim + 180) % 360 - 180)
            mesma = (a["nome"] and b["nome"] == a["nome"]) or (not a["nome"] and a["ref"] and b["ref"] == a["ref"])
            if mesma or (not a["nome"] and not a["ref"] and len(self.out_edges.get(v, [])) <= 2):
                if ang < melhor_ang:
                    melhor, melhor_ang = j, ang
            elif ang < reto_ang and (b["highway"] or "").replace("_link", "") == (a["highway"] or "").replace("_link", ""):
                reto, reto_ang = j, ang
        if melhor is not None and melhor_ang <= 75:
            return melhor
        if permitir_troca_nome and reto is not None and reto_ang <= 20:
            return reto
        return None

    def node_xy_de(self, n):
        return self.node_xy[n]

    def avancar(self, i, off, dist):
        """Anda `dist` metros pela mesma via a partir de (i, off). Devolve (i, off)."""
        off += dist
        visit = 0
        while off > self.attr[i]["len"] and visit < 50:
            j = self.continuacao_mesma_via(i)
            if j is None or not self.tem(j):
                return i, self.attr[i]["len"]
            off -= self.attr[i]["len"]
            i = j
            visit += 1
        return i, off

    def tem(self, i):
        """A aresta i está carregada? (Malha: sempre; MalhaTiles: só a região em volta do carro)."""
        return i is not None and 0 <= i < len(self.edges)

    def mesma_via(self, i, oi, j, oj):
        """Mesmo nome e mesmo sentido (<=90°)."""
        return self.attr[i]["nome"] == self.attr[j]["nome"] and \
            abs((self.rumo(i, oi) - self.rumo(j, oj) + 180) % 360 - 180) <= 90


class Malha(MalhaOps):
    """Arestas dirigidas com geometria em metros + índice espacial STRtree (tudo em memória)."""

    def __init__(self, G: nx.MultiDiGraph, nome="grafo"):
        self.nome = nome
        xs = {n: to_xy(d["y"], d["x"]) for n, d in G.nodes(data=True)}
        self.node_xy = xs
        self.edges = []        # (u, v, k)
        self.geoms = []
        self.attr = []
        self.R = Adj()  # grafo enxuto para rotas (menor custo entre u,v)
        self._cache_dij = {}
        for u, v, k, d in G.edges(keys=True, data=True):
            if "geometry" in d:
                xy = [to_xy(lat, lon) for lon, lat in d["geometry"].coords]
            else:
                xy = [xs[u], xs[v]]
            ls = LineString(xy)
            if ls.length < 0.5:
                continue
            idx = len(self.edges)
            self.edges.append((u, v, k))
            self.geoms.append(ls)
            self.attr.append(_attr_de(d, ls.length))
            self.R.add_edge(u, v, ls.length, idx)
        self.tree = STRtree(self.geoms)
        self.out_edges = {}
        for i, (u, v, k) in enumerate(self.edges):
            self.out_edges.setdefault(u, []).append(i)
        self.idx_of = {e: i for i, e in enumerate(self.edges)}

    def _consulta(self, geom):
        return self.tree.query(geom)

    def consultar_bbox(self, xmin, ymin, xmax, ymax):
        from shapely.geometry import box
        return [int(i) for i in self.tree.query(box(xmin, ymin, xmax, ymax))]


def carregar(path, nome=None) -> Malha:
    import osmnx as ox
    G = ox.load_graphml(path)
    return Malha(G, nome or str(path))
