"""UF de um ponto pela malha do IBGE (data/raw/ibge/uf_br.geojson, qualidade intermediária)."""
from __future__ import annotations

import json
from functools import lru_cache

from radar_pi import config

ARQ = config.RAW_DIR / "ibge" / "uf_br.geojson"
EST = config.RAW_DIR / "ibge" / "estados.json"


@lru_cache(maxsize=1)
def _indice():
    from shapely.geometry import shape
    from shapely.strtree import STRtree
    if not ARQ.exists():
        return None
    sig = {str(e["id"]): e["sigla"] for e in json.loads(EST.read_text())} if EST.exists() else {}
    feats = json.loads(ARQ.read_text())["features"]
    geoms = [shape(f["geometry"]) for f in feats]
    ufs = [sig.get(str(f["properties"]["codarea"]), f["properties"]["codarea"]) for f in feats]
    return STRtree(geoms), geoms, ufs


def uf_de(lat, lon) -> str | None:
    idx = _indice()
    if idx is None or lat is None or lon is None:
        return None
    from shapely.geometry import Point
    tree, geoms, ufs = idx
    p = Point(float(lon), float(lat))
    for i in tree.query(p):
        if geoms[i].covers(p):
            return ufs[i]
    i = tree.nearest(p)                       # pontos no litoral/divisa (malha simplificada): UF mais próxima
    return ufs[i] if geoms[i].distance(p) < 0.05 else None
