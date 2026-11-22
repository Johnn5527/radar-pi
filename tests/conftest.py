import json
import sqlite3
from pathlib import Path

import pytest

from radar_pi.dados import db
from radar_pi.mapa.grafo import Malha
from radar_pi.mapa.vinculo import vincular_todos
from radar_pi.sim.sintetico import ponto_ll

ROOT = Path(__file__).resolve().parent.parent
RES = ROOT / "resultados"
RES.mkdir(exist_ok=True)
METR = RES / "metricas.jsonl"


@pytest.fixture
def registrar(request):
    def _r(**kw):
        with open(METR, "a") as f:
            f.write(json.dumps({"teste": request.node.nodeid, **kw}, ensure_ascii=False, default=str) + "\n")
    return _r


def radar(id_, x, y, limite=50, sentido="CENTRO/BAIRRO", via=None, status="ativo", fonte="cet_sp", texto=None,
          municipio="SAO PAULO", tipo="FIXO"):
    lat, lon = ponto_ll(x, y)
    return {"id": f"{fonte}:{id_}", "fonte": fonte, "id_fonte": id_, "lat": lat, "lon": lon,
            "limite_kmh": limite, "limite_texto": texto or (str(limite) if limite else None), "sentido": sentido,
            "via": via, "municipio": municipio, "status": status, "tipo": tipo, "extra": "{}"}


def montar(construtor, radares, nome="sint"):
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript(db.SCHEMA)
    db.gravar_radares(con, radares)
    m = Malha(construtor.G, nome)
    info = vincular_todos(con, m)
    return m, con, info


def episodios(quadros):
    """Agrupa alertas consecutivos por radar: [{id, t_ini, d_ini, t_fim, d_fim, n, incerta}]"""
    eps, cur = [], None
    for q in quadros:
        a = q.alerta
        rid = a.radar_id if a else None
        if cur and rid != cur["id"]:
            eps.append(cur)
            cur = None
        if a and cur is None:
            cur = {"id": rid, "t_ini": q.t, "d_ini": round(a.distancia_m, 1), "n": 0, "incerta": False}
        if a:
            cur.update(t_fim=q.t, d_fim=round(a.distancia_m, 1))
            cur["n"] += 1
            cur["incerta"] = cur["incerta"] or a.via_incerta
    if cur:
        eps.append(cur)
    return eps


def _tiles():
    """N10: malha por tiles H3 em SQLite (cidade de SP + corredor da Tamoios), grafo 'sp'."""
    from radar_pi.mapa.tiles import MalhaTiles
    p = ROOT / "data/osm/malha_sp.sqlite"
    if not p.exists():
        pytest.skip("malha por tiles não gerada (scripts/construir_tiles.py)")
    return MalhaTiles(p, "sp")


@pytest.fixture(scope="session")
def malha_sp():
    return _tiles()


@pytest.fixture(scope="session")
def malha_tamoios():
    return _tiles()


@pytest.fixture(scope="session")
def con_real():
    p = ROOT / "data/radares.sqlite"
    if not p.exists():
        pytest.skip("banco não gerado")
    return db.conectar(p)
