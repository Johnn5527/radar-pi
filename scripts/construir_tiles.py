"""N10: converte as malhas OSM baixadas num SQLite de tiles H3 (data/osm/malha_sp.sqlite)."""
import os
import pickle
import sys
import time

from radar_pi.mapa.grafo import carregar
from radar_pi.mapa.tiles import construir

saida = "data/osm/malha_sp.sqlite"
if os.path.exists(saida):
    os.remove(saida)
t = time.time()
ms = []
with open("data/osm/sp_city.malha.pkl", "rb") as f:
    ms.append(pickle.load(f))          # cidade de SP (já projetada)
# tamoios.graphml (v1, só vias principais) NÃO entra: duplicaria arestas da tamoios_full
for g in ("data/osm/tamoios_full.graphml",):
    if os.path.exists(g):
        ms.append(carregar(g, "tamoios"))
n = construir(saida, ms)
print("arestas", n, "s", round(time.time() - t, 1), "MB", round(os.path.getsize(saida) / 1e6, 1))
