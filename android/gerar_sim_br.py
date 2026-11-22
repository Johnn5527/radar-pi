"""Trajetos de simulação em BRs (malha nacional do APK, mesmo código/compat do app) -> app/data/sim/."""
import json
import os
import sys
from pathlib import Path

APP = Path(__file__).resolve().parent / "app"
sys.path.insert(0, str(APP / "compat"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["RADAR_DB_PATH"] = str(APP / "data/radares.sqlite")
os.environ["RADAR_GRAPH_TILES"] = str(APP / "data/osm/malha_sp.sqlite")
from radar_pi import config  # noqa: E402
from radar_pi.alerta.motor import CONF_MIN  # noqa: E402
from radar_pi.dados import db  # noqa: E402
from radar_pi.mapa.tiles import MalhaTiles  # noqa: E402
from radar_pi.sim.rotas import caminho_ate_radar, gerar  # noqa: E402

CEN = {
    "br116_dnit": "r.fonte='dnit' AND r.via='BR-116' AND r.lat > -15",          # BR-116 no Nordeste (DNIT)
    "br116_sul": "r.fonte='dnit' AND r.via='BR-116' AND r.lat < -28",            # BR-116 no RS (DNIT)
    "sp348_artesp": "r.fonte='artesp' AND r.via='SP-348' AND r.limite_kmh IS NOT NULL",  # Bandeirantes (ARTESP)
}
out = APP / "data/sim"
res = {}
for nome, filtro in CEN.items():
    m = MalhaTiles(config.GRAPH_TILES, "sp")
    con = db.conectar()
    ok = False
    for r in con.execute(f"SELECT r.id, r.fonte, r.limite_kmh, v.u, v.v, v.k, v.offset_m FROM radares r JOIN vinculos v "
                         f"ON v.radar_id=r.id WHERE v.grafo='sp' AND r.status='ativo' AND v.confianca>=? AND {filtro} "
                         f"ORDER BY r.id", (CONF_MIN,)).fetchall():
        try:
            i = m.idx_of[(r["u"], r["v"], r["k"])]
            ar, s = caminho_ate_radar(m, i, r["offset_m"], 1100, 200)
            ini = max(0.0, s - 900)
            if s - ini < 850:
                continue
            fx, _ = gerar(m, ar, seed=1, inicio_m=ini, fim_m=s + 150, vel_func=lambda sa: 100)
        except Exception:  # noqa: BLE001
            continue
        (out / f"{nome}.json").write_text(json.dumps({"cenario": nome, "info": {"radar": r["id"], "s_radar": s - ini},
                                                      "fixes": [[f.t, f.lat, f.lon, f.vel_ms, f.rumo, f.precisao_m] for f in fx]}))
        res[nome] = (r["id"], r["fonte"], r["limite_kmh"], len(fx), round(fx[0].lat, 3), round(fx[0].lon, 3))
        ok = True
        break
    if not ok:
        res[nome] = None
ordem = json.loads((out / "ordem.json").read_text())
for n in CEN:
    if res.get(n) and n not in ordem:
        ordem.insert(0, n)
(out / "ordem.json").write_text(json.dumps(ordem))
print(json.dumps(res, ensure_ascii=False))
