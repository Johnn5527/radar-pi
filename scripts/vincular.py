"""Vincula os radares à malha por tiles (data/osm/malha_sp.sqlite), grafo 'sp'."""
import time

import psutil

from radar_pi.dados import db
from radar_pi.mapa.tiles import MalhaTiles
from radar_pi.mapa.vinculo import vincular_todos

t = time.time()
m = MalhaTiles("data/osm/malha_sp.sqlite", "sp")
con = db.conectar()
r = vincular_todos(con, m, (-24.01, -46.83, -23.15, -45.35))
print("sp", r, "s", round(time.time() - t, 1), "rss_MB", round(psutil.Process().memory_info().rss / 1e6), "cargas", m.cargas)
