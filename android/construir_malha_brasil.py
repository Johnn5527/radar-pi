"""Malha OSM NACIONAL compacta para o APK (offline), a partir do brazil-latest.osm.pbf (Geofabrik) da box.

- Brasil inteiro: motorway/trunk/primary/secondary (+ _link), com nome e ref (ex.: BR-116);
- Malha urbana completa (tertiary, residential, living_street, unclassified) num raio de ~1,1 km de cada
  radar do banco do APK (todas as fontes oficiais coletadas);
- Geometria simplificada (Douglas-Peucker, 2,5 m) entre cruzamentos; grafo dirigido (oneway respeitado);
- Mesmo esquema de tiles de radar_pi.mapa.tiles, células pela grade do compat/h3 (a mesma do APK).
Uso: PYTHONPATH=. .venv/bin/python android/construir_malha_estado.py data/osm/estado/sp_estado_hw.pbf saida.sqlite"""
import importlib.util
import json
import sqlite3
import sys
import time
from array import array
from pathlib import Path

import osmium
from pyproj import Transformer
from shapely.geometry import LineString, Point, shape
from shapely.prepared import prep

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from radar_pi.mapa.tiles import RES, SCHEMA, _xy_blob  # noqa: E402

spec = importlib.util.spec_from_file_location("compat_h3", ROOT / "android/compat/h3/__init__.py")
H3C = importlib.util.module_from_spec(spec)
spec.loader.exec_module(H3C)

FWD = Transformer.from_crs("EPSG:4326", "EPSG:31983", always_xy=True)
INV = Transformer.from_crs("EPSG:31983", "EPSG:4326", always_xy=True)
PRINCIPAIS = {"motorway", "motorway_link", "trunk", "trunk_link", "primary", "primary_link", "secondary",
              "secondary_link"}
LOCAIS = {"tertiary", "tertiary_link", "residential", "living_street", "unclassified", "road"}
GRADE = 0.005         # graus (~550 m)
RAIO_CEL = 2          # ±2 células ≈ 1,1 km em volta do radar
PERTO = set()


def marcar_radares(db_path):
    con = sqlite3.connect(db_path)
    n = 0
    for la, lo in con.execute("SELECT lat, lon FROM radares WHERE lat IS NOT NULL AND status='ativo'"):
        i, j = int(la // GRADE), int(lo // GRADE)
        for a in range(-RAIO_CEL, RAIO_CEL + 1):
            for b in range(-RAIO_CEL, RAIO_CEL + 1):
                PERTO.add((i + a, j + b))
        n += 1
    return n
# (lat_min, lon_min, lat_max, lon_max)
DETALHE = [(-24.10, -47.25, -23.15, -45.65),   # RMSP
           (-23.10, -47.35, -22.65, -46.85),   # Campinas
           (-24.25, -46.75, -23.80, -45.95),   # Baixada Santista
           (-23.40, -46.10, -22.60, -44.90),   # Vale do Paraíba / SJC
           (-23.65, -47.60, -23.35, -47.30),   # Sorocaba
           (-21.30, -47.95, -21.05, -47.70)]   # Ribeirão Preto
SIMPLIFICA_M = 3.0


def no_detalhe(lat, lon):
    return (int(lat // GRADE), int(lon // GRADE)) in PERTO


def poligono_sp():
    d = json.load(open(ROOT / "data/raw/ibge/uf_br.geojson"))
    f = next(f for f in d["features"] if f["properties"].get("codarea") == "35")
    return prep(shape(f["geometry"]).buffer(0.01))


class Coletor(osmium.SimpleHandler):
    def __init__(self, sp):
        super().__init__()
        self.sp = sp
        self.vias = []
        self.uso = {}

    def way(self, w):
        hw = w.tags.get("highway")
        nome = w.tags.get("name")
        if hw not in PRINCIPAIS and hw not in LOCAIS:
            return
        try:
            nos = [(n.ref, n.lat, n.lon) for n in w.nodes]
        except osmium.InvalidLocationError:
            nos = [(n.ref, n.lat, n.lon) for n in w.nodes if n.location.valid()]
        if len(nos) < 2:
            return
        meio = nos[len(nos) // 2]
        if hw in LOCAIS and not any(no_detalhe(la, lo) for _, la, lo in (nos[0], meio, nos[-1])):
            return
        ow = w.tags.get("oneway", "")
        junc = w.tags.get("junction", "")
        sentido = -1 if ow == "-1" else (1 if ow in ("yes", "true", "1") or hw == "motorway" or junc in ("roundabout", "circular") else 0)
        self.vias.append((array("q", [n[0] for n in nos]), array("d", [c for n in nos for c in (n[1], n[2])]),
                          hw, nome, w.tags.get("ref"), w.tags.get("maxspeed"), sentido))
        for n in nos:
            self.uso[n[0]] = self.uso.get(n[0], 0) + 1


def main(pbf, saida):
    t0 = time.time()
    print("radares marcados:", marcar_radares(ROOT / "android/app/data/radares.sqlite"), "células", len(PERTO))
    col = Coletor(None)
    col.apply_file(pbf, locations=True, idx="flex_mem")
    print("vias", len(col.vias), "nós usados", len(col.uso), round(time.time() - t0), "s", flush=True)
    Path(saida).unlink(missing_ok=True)
    con = sqlite3.connect(saida)
    con.executescript("PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF;" + SCHEMA.replace(
        "CREATE UNIQUE INDEX IF NOT EXISTS ix_uvk ON arestas(u, v, k);", "").replace(
        "CREATE INDEX IF NOT EXISTS ix_cel ON aresta_cel(cel);", ""))
    nos_xy = {}
    k_uv = {}
    lote_a, lote_c = [], []
    n_ar = 0
    eid = 0
    for ids, cs, hw, nome, ref, ms, sentido in col.vias:
        pts = [(cs[2 * i], cs[2 * i + 1]) for i in range(len(ids))]
        corte = [0] + [i for i in range(1, len(ids) - 1) if col.uso.get(ids[i], 0) > 1] + [len(ids) - 1]
        for a, b in zip(corte, corte[1:]):
            xy = [FWD.transform(lo, la) for la, lo in pts[a:b + 1]]
            ls = LineString(xy)
            if ls.length < 0.5:
                continue
            ls = ls.simplify(SIMPLIFICA_M, preserve_topology=False)
            xy = list(ls.coords)
            u, v = ids[a], ids[b]
            nos_xy[u], nos_xy[v] = xy[0], xy[-1]
            dirs = [(u, v, xy)] if sentido == 1 else ([(v, u, xy[::-1])] if sentido == -1 else [(u, v, xy), (v, u, xy[::-1])])
            cels = set()
            for x, y in xy + [(ls.interpolate(d).x, ls.interpolate(d).y) for d in range(500, int(ls.length), 500)]:
                lon, lat = INV.transform(x, y)
                cels.add(H3C.latlng_to_cell(lat, lon, RES))
            for uu, vv, g in dirs:
                k = k_uv.get((uu, vv), 0)
                k_uv[(uu, vv)] = k + 1
                eid += 1
                lote_a.append((eid, uu, vv, k, nome, ref, hw, ms, 1 if sentido else 0, _xy_blob(g)))
                lote_c.extend((c, eid) for c in cels)
                n_ar += 1
        if len(lote_a) > 200000:
            con.executemany("INSERT INTO arestas VALUES(?,?,?,?,?,?,?,?,?,?)", lote_a)
            con.executemany("INSERT INTO aresta_cel VALUES(?,?)", lote_c)
            lote_a, lote_c = [], []
    con.executemany("INSERT INTO arestas VALUES(?,?,?,?,?,?,?,?,?,?)", lote_a)
    con.executemany("INSERT INTO aresta_cel VALUES(?,?)", lote_c)
    con.executemany("INSERT INTO nos VALUES(?,?,?)", [(n, x, y) for n, (x, y) in nos_xy.items()])
    con.execute("CREATE UNIQUE INDEX ix_uvk ON arestas(u, v, k)")
    con.execute("CREATE INDEX ix_cel ON aresta_cel(cel)")
    con.execute("INSERT OR REPLACE INTO meta VALUES('res', ?)", (str(RES),))
    con.execute("INSERT OR REPLACE INTO meta VALUES('indice', 'compat_grade_quadrada')")
    con.execute("INSERT OR REPLACE INTO meta VALUES('cobertura', 'brasil: motorway..secondary(+links) + malha urbana completa a ~1,1 km dos radares')")
    con.commit()
    con.execute("VACUUM")
    con.close()
    print("arestas", n_ar, "nós", len(nos_xy), "MB", round(Path(saida).stat().st_size / 1e6, 1), round(time.time() - t0), "s")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
