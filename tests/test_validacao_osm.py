"""Item 1/2: cruzamento com OSM e status_validacao (confirmado / nao_verificado / conflito)."""
import csv
import json
import subprocess
import sys
from pathlib import Path

import pytest

from radar_pi.dados import db
from radar_pi.validacao import osm as V
from radar_pi.validacao import status as S

ROOT = Path(__file__).resolve().parent.parent
LAT0, LON0 = -23.55, -46.63


def desl(m_norte=0.0, m_leste=0.0):
    import math
    return LAT0 + m_norte / 111_320, LON0 + m_leste / (111_320 * math.cos(math.radians(LAT0)))


def radar(i, fonte, n=0, e=0, lim=50, status="ativo", dup=None, extra=None, lim_origem="oficial"):
    lat, lon = desl(n, e)
    return {"id": i, "fonte": fonte, "id_fonte": i, "lat": lat, "lon": lon, "limite_kmh": lim, "status": status,
            "duplicata_de": dup, "extra": json.dumps(extra or {}), "limite_origem": lim_origem}


def osm(i, n=0, e=0, ms=50):
    lat, lon = desl(n, e)
    return {"osm_id": f"node/{i}", "lat": lat, "lon": lon, "maxspeed": ms}


def test_parse_maxspeed_e_csv_seguro():
    assert [V.parse_maxspeed(x) for x in ("60", "60 km/h", "60;40", "40 mph", "none", "signals", None, "999")] == \
        [60, 60, 60, 64, None, None, None, None]
    assert V.seguro_csv("=HYPERLINK(1)") == "'=HYPERLINK(1)" and V.seguro_csv(-23.5) == -23.5
    with pytest.raises(ValueError):
        V.query_overpass('BR"];out;')            # área injetada é rejeitada
    assert '"ISO3166-2"="BR-SP"' in V.query_overpass("BR-SP")


def test_cruzar_categorias_e_coordenada_invalida():
    base = [{"id": "a", "lat": desl()[0], "lon": desl()[1], "limite": 50},
            {"id": "b", "lat": desl(1000)[0], "lon": desl(1000)[1], "limite": 60},
            {"id": "c", "lat": desl(3000)[0], "lon": desl(3000)[1], "limite": 70},
            {"id": "x", "lat": -20.9, "lon": 48.46, "limite": 60}]           # sinal da longitude trocado
    o = [osm(1, 15, 0, 40), osm(2, 1090), osm(3, 6000)]
    linhas, so, inval = V.cruzar(base, o)
    cat = {l["id"]: l["categoria"] for l in linhas}
    assert cat == {"a": "confirmado_osm", "b": "proximo_osm", "c": "so_base"}
    assert [x["osm_id"] for x in so] == ["node/3"] and [x["id"] for x in inval] == ["x"]
    assert next(l for l in linhas if l["id"] == "a")["limite_divergente"] is True


def test_status_validacao_regras():
    rs = [radar("cet:1", "cet_sp"),                                   # + OSM 20 m mesmo limite -> confirmado
          radar("cet:2", "cet_sp", n=500),                            # nada -> nao_verificado
          radar("cet:3", "cet_sp", n=1000),                           # OSM a 90 m -> nao_verificado (proximo)
          radar("cet:4", "cet_sp", n=1500, lim=50),                   # OSM 10 m limite 60 -> conflito
          radar("der:5", "der_sp", n=2000, lim=80), radar("art:5", "artesp", n=2010, lim=80, dup="der:5"),  # 2 oficiais
          radar("der:6", "der_sp", n=2500, lim=80), radar("art:6", "artesp", n=2505, lim=60, dup="der:6"),  # limites ≠
          radar("antt:7", "antt_via_espelho", n=3000, extra={"source": "ANTT+OSM"}),  # OSM não é independente
          radar("der:8", "der_sp", n=3500), radar("cet:8", "cet_sp", n=3510, status="inativo"),  # inativo em outra fonte
          radar("cet:9", "cet_sp", n=4000), radar("cet:9b", "cet_sp", n=4005, status="inativo"),  # mesma fonte: troca
          radar("cet:10", "cet_sp", n=4500, lim_origem="inmetro")]   # Inmetro casou -> 2 fontes
    o = [osm(1, 20), osm(3, 1090), osm(4, 1510, ms=60), osm(7, 3005), osm(99, 9000)]
    upd, so = S.classificar(rs, o)
    st = {k: (v["status_validacao"], v["n_fontes"]) for k, v in upd.items()}
    assert st["cet:1"] == ("confirmado", 2)
    assert st["cet:2"] == ("nao_verificado", 1)
    assert st["cet:3"] == ("nao_verificado", 1) and upd["cet:3"]["categoria_osm"] == "proximo_osm"
    assert st["cet:4"][0] == "conflito" and "limite_divergente_osm:50x60" in upd["cet:4"]["motivo_validacao"]
    assert st["der:5"] == ("confirmado", 2) and st["art:5"] == ("confirmado", 2)
    assert st["der:6"][0] == "conflito"
    assert st["antt:7"] == ("nao_verificado", 1)
    assert st["der:8"][0] == "conflito" and "inativo_em_outra_fonte" in upd["der:8"]["motivo_validacao"]
    assert st["cet:9"] == ("nao_verificado", 1)
    assert st["cet:10"] == ("confirmado", 2)
    assert [x["osm_id"] for x in so] == ["node/99"]


def test_aplicar_no_banco_nao_remove_nem_alerta_so_osm(tmp_path):
    con = db.conectar(tmp_path / "r.sqlite")
    rows = [radar("cet:1", "cet_sp"), radar("cet:2", "cet_sp", n=500), radar("cet:3", "cet_sp", n=900, status="inativo")]
    for r in rows:
        r["extra"] = json.loads(r["extra"])
        r["extra"] = json.dumps(r["extra"])
    db.gravar_radares(con, [dict(r) for r in rows])
    antes = [tuple(r) for r in con.execute("SELECT id, status, duplicata_de, lat, lon, limite_kmh FROM radares ORDER BY id")]
    res = S.aplicar(con, [osm(1, 10), osm(50, 5000)], "teste")
    depois = [tuple(r) for r in con.execute("SELECT id, status, duplicata_de, lat, lon, limite_kmh FROM radares ORDER BY id")]
    assert antes == depois                                    # nada removido/alterado fora das colunas de validação
    assert res["status"] == {"confirmado": 1, "nao_verificado": 1} and res["revisao_osm"] == 1
    assert con.execute("SELECT count(*) FROM revisao_osm").fetchone()[0] == 1
    lat, lon = desl(5000)
    assert not any(r["id"].startswith("node/") for r in db.radares_proximos(con, lat, lon, k=3))  # so_osm não alerta
    assert con.execute("SELECT status_validacao FROM radares WHERE id='cet:3'").fetchone()[0] is None  # inativo: sem estado


def test_cli_geojson_e_csv_ponto_e_virgula(tmp_path):
    base = tmp_path / "b.csv"
    la, lo = desl()
    base.write_text(f"id;latitude;longitude;limite;uf\nr1;{str(la).replace('.', ',')};{str(lo).replace('.', ',')};50;SP\n"
                    f"=cmd;{desl(800)[0]};{desl(800)[1]};60;SP\n", encoding="utf-8")
    gj = tmp_path / "o.geojson"
    gj.write_text(json.dumps({"type": "FeatureCollection", "features": [
        {"type": "Feature", "id": "node/1", "properties": {"maxspeed": "60"}, "geometry": {"type": "Point", "coordinates": [lo, la]}},
        {"type": "Feature", "id": "node/2", "properties": {}, "geometry": {"type": "Point", "coordinates": [desl(0, 5000)[1], desl(0, 5000)[0]]}}]}))
    out = tmp_path / "div.csv"
    r = subprocess.run([sys.executable, "scripts/cruzar_osm.py", str(base), "--geojson", str(gj), "--saida", str(out)],
                       cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    res = json.loads(r.stdout)
    assert res["categorias"] == {"confirmado_osm": 1, "so_base": 1, "so_osm": 1} and res["limite_divergente"] == 1
    linhas = list(csv.DictReader(out.open()))
    assert any(l["id"] == "'=cmd" for l in linhas)            # injeção de fórmula neutralizada
