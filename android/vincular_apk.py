"""Vincula TODOS os radares do banco do APK à malha nacional do APK, com o mesmo código do app
(bibliotecas puras de android/compat + grade do compat/h3). Grafo 'sp' (nome histórico usado pelo app)."""
import os
import sys
import time
from pathlib import Path

APP = Path(__file__).resolve().parent / "app"
sys.path.insert(0, str(APP / "compat"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["RADAR_DB_PATH"] = str(APP / "data/radares.sqlite")
os.environ["RADAR_GRAPH_TILES"] = str(APP / "data/osm/malha_sp.sqlite")
import h3  # noqa: E402
assert "compat" in h3.__file__
from radar_pi import config  # noqa: E402
from radar_pi.dados import db  # noqa: E402
from radar_pi.mapa.tiles import MalhaTiles  # noqa: E402
from radar_pi.mapa.vinculo import vincular_todos  # noqa: E402

t = time.time()
m = MalhaTiles(config.GRAPH_TILES, "sp")
con = db.conectar()
r = vincular_todos(con, m, None)
print("vínculos:", r, round(time.time() - t), "s")
