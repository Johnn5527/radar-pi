"""Roda o Navegador (HMM + alerta) em todos os trajetos de data/sim e imprime um JSON por fix.
Uso: python _rodar_cenarios.py real|compat  (compat = bibliotecas puras + bancos re-indexados do APK)."""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "android" / "app"
modo = sys.argv[1]
if modo == "compat":
    sys.path.insert(0, str(APP / "compat"))
    os.environ["RADAR_DB_PATH"] = str(APP / "data/radares.sqlite")
    os.environ["RADAR_GRAPH_TILES"] = str(APP / "data/osm/malha_sp.sqlite")
sys.path.insert(0, str(ROOT))
os.environ["RADAR_TELEMETRIA"] = "0"
import h3, shapely, pyproj  # noqa: E402,F401
assert ("compat" in h3.__file__) == (modo == "compat"), h3.__file__
from radar_pi import config  # noqa: E402
from radar_pi.dados import db  # noqa: E402
from radar_pi.mapa.tiles import MalhaTiles  # noqa: E402
from radar_pi.mapa_ui.navegador import Navegador  # noqa: E402
from radar_pi.mapmatch.hmm import Fix  # noqa: E402


class VozNula:
    def falar(self, texto, t=None):
        return {"texto": texto}


out = {}
for nome in json.loads((APP / "data/sim/ordem.json").read_text()):
    d = json.loads((APP / f"data/sim/{nome}.json").read_text())
    m = MalhaTiles(config.GRAPH_TILES, "sp")
    nav = Navegador(m, db.conectar(), VozNula())
    linhas = []
    for x in d["fixes"]:
        f = Fix(*x)
        nav.novo_fix(f, f.t)
        e = nav.quadro(f.t, 0.05)
        a = nav.alerta
        linhas.append({"t": round(f.t, 1), "via": nav.via,
                       "alerta": [a.radar_id, round(a.distancia_m), a.limite_kmh] if a else None,
                       "placa": [nav.placa.get("limite"), round(nav.placa["dist"])] if nav.placa and "dist" in nav.placa else None,
                       "lim_via": nav.limite_via, "x": round(e["cx"], 1) if e else None})
    out[nome] = {"linhas": linhas, "voz": [(round(t, 1), r["texto"]) for t, r in nav.eventos_voz]}
print(json.dumps(out, ensure_ascii=False))
