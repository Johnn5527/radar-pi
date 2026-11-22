"""Malhas sintéticas (em metros locais) para cenários controlados de teste."""
from __future__ import annotations

import math

import networkx as nx
from shapely.geometry import LineString

from radar_pi.mapa.grafo import to_latlon, to_xy

BASE = to_xy(-23.55, -46.63)


def _ll(x, y):
    return to_latlon(BASE[0] + x, BASE[1] + y)


class Construtor:
    def __init__(self):
        self.G = nx.MultiDiGraph(crs="EPSG:4326")
        self._nos = {}

    def no(self, x, y):
        key = (round(x, 2), round(y, 2))
        if key not in self._nos:
            n = len(self._nos) + 1
            lat, lon = _ll(x, y)
            self.G.add_node(n, x=lon, y=lat)
            self._nos[key] = n
        return self._nos[key]

    def via(self, pontos, nome, mao_dupla=True, highway="primary", maxspeed=None, seg_m=None):
        """Cria uma via pelos pontos (x,y em m). seg_m: quebra em arestas a cada N m."""
        if seg_m:
            ls = LineString(pontos)
            n = max(1, int(ls.length // seg_m))
            pontos = [(ls.interpolate(ls.length * k / n).x, ls.interpolate(ls.length * k / n).y) for k in range(n + 1)]
        nos = [self.no(*p) for p in pontos]
        for a, b, pa, pb in zip(nos, nos[1:], pontos, pontos[1:]):
            attrs = {"name": nome, "highway": highway, "oneway": not mao_dupla}
            if maxspeed:
                attrs["maxspeed"] = str(maxspeed)
            self.G.add_edge(a, b, **attrs)
            if mao_dupla:
                self.G.add_edge(b, a, **attrs)
        return nos

    def via_curva(self, centro, raio, ang0, ang1, nome, n=24, **kw):
        pts = [(centro[0] + raio * math.cos(math.radians(a)), centro[1] + raio * math.sin(math.radians(a)))
               for a in [ang0 + (ang1 - ang0) * k / n for k in range(n + 1)]]
        return self.via(pts, nome, **kw), pts


def ponto_ll(x, y):
    return _ll(x, y)
