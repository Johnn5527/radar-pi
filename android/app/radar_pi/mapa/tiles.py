"""N10: malha em SQLite por tiles H3 (res. 7, ~5 km²), carregada sob demanda em volta do carro.
Memória proporcional à região carregada, não à cidade/estado inteiro."""
from __future__ import annotations

import sqlite3
from array import array

import h3
from shapely import STRtree
from shapely.geometry import LineString

from .grafo import Adj, MalhaOps, _attr_de, to_latlon, to_xy

RES = 7

SCHEMA = """
CREATE TABLE IF NOT EXISTS nos(id INTEGER PRIMARY KEY, x REAL, y REAL);
CREATE TABLE IF NOT EXISTS arestas(id INTEGER PRIMARY KEY, u INTEGER, v INTEGER, k INTEGER, nome TEXT,
  ref TEXT, highway TEXT, maxspeed TEXT, oneway INTEGER, xy BLOB);
CREATE UNIQUE INDEX IF NOT EXISTS ix_uvk ON arestas(u, v, k);
CREATE TABLE IF NOT EXISTS aresta_cel(cel TEXT, aresta INTEGER);
CREATE INDEX IF NOT EXISTS ix_cel ON aresta_cel(cel);
CREATE TABLE IF NOT EXISTS meta(chave TEXT PRIMARY KEY, valor TEXT);
"""


def _xy_blob(coords):
    a = array("i")
    for x, y in coords:
        a.append(int(round(x * 10)))
        a.append(int(round(y * 10)))
    return a.tobytes()


def _blob_xy(b):
    a = array("i")
    a.frombytes(b)
    return [(a[i] / 10.0, a[i + 1] / 10.0) for i in range(0, len(a), 2)]


def construir(saida, malhas, res=RES):
    """Grava 1+ objetos Malha (em memória) num SQLite de tiles. Nós OSM são globais: malhas se juntam."""
    con = sqlite3.connect(saida)
    con.executescript(SCHEMA)
    n_ar = 0
    for m in malhas:
        con.executemany("INSERT OR IGNORE INTO nos VALUES(?,?,?)", [(int(n), x, y) for n, (x, y) in m.node_xy.items()])
        for i, (u, v, k) in enumerate(m.edges):
            a = m.attr[i]
            coords = list(m.geoms[i].coords)
            nome = a["nome_orig"][0] if isinstance(a["nome_orig"], list) else a["nome_orig"]
            cur = con.execute("INSERT OR IGNORE INTO arestas(u,v,k,nome,ref,highway,maxspeed,oneway,xy) VALUES(?,?,?,?,?,?,?,?,?)",
                              (int(u), int(v), int(k), nome if isinstance(nome, str) else None, a["ref"], a["highway"],
                               str(a["maxspeed"]) if a["maxspeed"] else None, int(a["oneway"]), _xy_blob(coords)))
            if cur.rowcount == 0:
                continue
            eid = cur.lastrowid
            n_ar += 1
            # células H3 de cada vértice + pontos intermediários a cada ~500 m
            ls = m.geoms[i]
            pts = coords + [(ls.interpolate(d).x, ls.interpolate(d).y) for d in range(500, int(ls.length), 500)]
            cels = {h3.latlng_to_cell(*to_latlon(x, y), res) for x, y in pts}
            con.executemany("INSERT INTO aresta_cel VALUES(?,?)", [(c, eid) for c in cels])
    con.execute("INSERT OR REPLACE INTO meta VALUES('res', ?)", (str(res),))
    con.commit()
    return n_ar


class MalhaTiles(MalhaOps):
    def __init__(self, path, nome="sp", k_anel=2, manter_anel=4):
        self.path, self.nome = str(path), nome
        self.con = sqlite3.connect(self.path, check_same_thread=False)
        self.k, self.manter = k_anel, manter_anel
        # acesso a uma aresta fora da região carregada a busca no SQLite (ex.: vínculo de radar distante)
        self.edges, self.geoms, self.attr = _AutoDict(self), _AutoDict(self), _AutoDict(self)
        self.coords = {}
        self.R = Adj()
        self.out_edges = {}
        self.node_xy = {}
        self._cache_dij = {}
        self.cel_arestas = {}     # célula -> ids
        self.cel_tree = {}        # célula -> (STRtree, ids)
        self.ref = {}             # aresta -> nº de células carregadas que a contêm
        self.centro = None
        self.cargas = 0
        self._pendentes = []
        self.idx_of = _IdxLazy(self)

    # --- carga ------------------------------------------------------------------
    def _add_aresta(self, row):
        eid, u, v, k, nome, ref, hw, ms, ow, xy = row
        if eid in self.edges:
            return
        coords = _blob_xy(xy)
        ls = LineString(coords)
        self.edges[eid] = (u, v, k)
        self.geoms[eid] = ls
        self.attr[eid] = _attr_de({"name": nome, "ref": ref, "highway": hw, "maxspeed": ms, "oneway": ow}, ls.length)
        self.R.add_edge(u, v, ls.length, eid)
        self.out_edges.setdefault(u, []).append(eid)
        self.node_xy.setdefault(u, coords[0])
        self.node_xy.setdefault(v, coords[-1])

    def _rem_aresta(self, eid):
        u, v, _ = self.edges.pop(eid)
        self.geoms.pop(eid, None)
        self.attr.pop(eid, None)
        self.coords.pop(eid, None)
        self.R.remove_edge_id(u, v, eid)
        oe = self.out_edges.get(u)
        if oe and eid in oe:
            oe.remove(eid)
            if not oe:
                del self.out_edges[u]

    def _carregar_cel(self, c):
        rows = self.con.execute("SELECT a.id,a.u,a.v,a.k,a.nome,a.ref,a.highway,a.maxspeed,a.oneway,a.xy FROM aresta_cel ac "
                                "JOIN arestas a ON a.id=ac.aresta WHERE ac.cel=?", (c,)).fetchall()
        ids = []
        for r in rows:
            self._add_aresta(r)
            self.ref[r[0]] = self.ref.get(r[0], 0) + 1
            ids.append(r[0])
        self.cel_arestas[c] = ids
        self.cel_tree[c] = (STRtree([self.geoms[i] for i in ids]), ids) if ids else (None, [])

    def garantir(self, x, y):
        """Anel 1 (7 células) carregado na hora; o resto do anel k entra 1 célula por chamada
        (carga incremental: evita travar o quadro ao cruzar a borda de um tile)."""
        c = h3.latlng_to_cell(*to_latlon(x, y), RES)
        if c == self.centro:
            if self._pendentes:
                self._carregar_cel(self._pendentes.pop(0))
                self._cache_dij.clear()
            return
        self.centro = c
        essenciais = [cc for cc in h3.grid_disk(c, 1) if cc not in self.cel_arestas]
        if essenciais:
            self.cargas += 1
            for cc in essenciais:
                self._carregar_cel(cc)
            self._cache_dij.clear()
        self._pendentes = [cc for cc in h3.grid_disk(c, self.k) if cc not in self.cel_arestas]
        manter = set(h3.grid_disk(c, self.manter))
        for cc in [cc for cc in self.cel_arestas if cc not in manter]:
            for eid in self.cel_arestas.pop(cc):
                self.ref[eid] -= 1
                if self.ref[eid] <= 0:
                    del self.ref[eid]
                    self._rem_aresta(eid)
            self.cel_tree.pop(cc, None)
            self._cache_dij.clear()

    def _consulta(self, geom):
        c = geom.centroid
        cel = h3.latlng_to_cell(*to_latlon(c.x, c.y), RES)
        vist = set()
        for cc in h3.grid_disk(cel, 1):
            t = self.cel_tree.get(cc)
            if not t or t[0] is None:
                continue
            for j in t[0].query(geom):
                vist.add(t[1][int(j)])
        return list(vist)

    def consultar_bbox(self, xmin, ymin, xmax, ymax):
        from shapely.geometry import box
        b = box(xmin, ymin, xmax, ymax)
        vist = set()
        for t, ids in self.cel_tree.values():
            if t is not None:
                for j in t.query(b):
                    vist.add(ids[int(j)])
        return list(vist)

    def coords_np(self, eid):
        import numpy as np
        a = self.coords.get(eid)
        if a is None:
            a = self.coords[eid] = np.asarray(self.geoms[eid].coords, dtype=np.float64)
        return a

    def node_xy_de(self, n):
        if n not in self.node_xy:
            r = self.con.execute("SELECT x,y FROM nos WHERE id=?", (n,)).fetchone()
            self.node_xy[n] = (r[0], r[1])
        return self.node_xy[n]

    def carregar_aresta(self, eid):
        """Carrega uma aresta avulsa (ex.: vínculo de radar fora da região ainda não carregada)."""
        if eid not in self.edges:
            r = self.con.execute("SELECT id,u,v,k,nome,ref,highway,maxspeed,oneway,xy FROM arestas WHERE id=?", (eid,)).fetchone()
            if r:
                self._add_aresta(r)

    def tem(self, i):
        return i in self.edges

    def memoria_estimada(self):
        return {"arestas_carregadas": len(self.edges), "celulas": len(self.cel_arestas)}


class _AutoDict(dict):
    def __init__(self, m):
        super().__init__()
        self.m = m

    def __missing__(self, eid):
        self.m.carregar_aresta(eid)
        if not dict.__contains__(self, eid):
            raise KeyError(eid)
        return dict.__getitem__(self, eid)


class _IdxLazy:
    def __init__(self, m):
        self.m = m

    def get(self, uvk, default=None):
        r = self.m.con.execute("SELECT id FROM arestas WHERE u=? AND v=? AND k=?", tuple(int(x) for x in uvk)).fetchone()
        if not r:
            return default
        self.m.carregar_aresta(r[0])
        return r[0]

    def __getitem__(self, uvk):
        r = self.get(uvk)
        if r is None:
            raise KeyError(uvk)
        return r
