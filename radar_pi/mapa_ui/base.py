"""Camada de desenho sobre a malha: 1 geometria por trecho (sem duplicar a mão dupla) e classe
visual por tipo de via. v2 (N10): funciona com a malha por tiles — consulta só o que está na tela,
sem pré-processar a cidade inteira."""
from __future__ import annotations

import numpy as np

from radar_pi.mapa.grafo import MalhaOps

# classe: (largura em metros, cor preenchimento, cor contorno, prioridade de rótulo)
CLASSES = {
    "motorway": (22, (252, 214, 140), (226, 172, 80), 0),
    "trunk": (20, (252, 214, 140), (226, 172, 80), 0),
    "primary": (16, (255, 236, 170), (222, 190, 110), 1),
    "secondary": (13, (255, 255, 255), (200, 200, 205), 2),
    "tertiary": (11, (255, 255, 255), (205, 205, 210), 3),
    "residential": (8, (255, 255, 255), (210, 210, 214), 4),
    "unclassified": (8, (255, 255, 255), (210, 210, 214), 4),
    "living_street": (7, (255, 255, 255), (214, 214, 218), 5),
    "service": (5, (250, 250, 250), (218, 218, 222), 6),
}
PADRAO = (7, (255, 255, 255), (212, 212, 216), 5)


def classe(hw):
    hw = (hw or "").replace("_link", "")
    return CLASSES.get(hw, PADRAO)


class MapaBase:
    def __init__(self, m: MalhaOps):
        self.m = m
        self._coords = {}

    def coords(self, i):
        c = self._coords.get(i)
        if c is None:
            if len(self._coords) > 60000:
                self._coords.clear()
            c = self._coords[i] = np.asarray(self.m.geoms[i].coords, dtype=np.float64)
        return c

    def cls(self, i):
        return classe(self.m.attr[i]["highway"])

    def visiveis(self, xmin, ymin, xmax, ymax):
        """Ids de aresta na janela, sem o par reverso da mão dupla, vias finas primeiro."""
        self.m.garantir((xmin + xmax) / 2, (ymin + ymax) / 2)
        vistos, out = set(), []
        for i in self.m.consultar_bbox(xmin, ymin, xmax, ymax):
            u, v, k = self.m.edges[i]
            par = (min(u, v), max(u, v), k)
            if par in vistos:
                continue
            vistos.add(par)
            out.append(i)
        out.sort(key=lambda i: self.cls(i)[0])
        return out
