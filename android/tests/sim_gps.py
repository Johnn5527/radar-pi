"""Box: simula sequências de android.location.Location (parado, andando em BR, salto, fora da área) passando
pelo MESMO GPSAndroid._novo (filtro de velocidade/precisão/cobertura) + Navegador + Renderer do APK.
Gera PNGs em android/resultados/ e imprime o log por fix. Uso: python android/tests/sim_gps.py"""
import json
import math
import os
import random
import sqlite3
import sys
from pathlib import Path

AND = Path(__file__).resolve().parents[1]
APP = AND / "app"
sys.path.insert(0, str(APP / "compat"))
sys.path.insert(0, str(APP))
os.environ.update(SDL_VIDEODRIVER="dummy", SDL_AUDIODRIVER="dummy", RADAR_TELEMETRIA="0")
import pygame  # noqa: E402
from android_rt import plataforma as plat  # noqa: E402
from radar_pi import config  # noqa: E402
from radar_pi.dados import db  # noqa: E402
from radar_pi.mapa.tiles import RES, MalhaTiles  # noqa: E402
from radar_pi.mapa_ui import render as rmod  # noqa: E402
from radar_pi.mapa_ui.base import MapaBase  # noqa: E402
from radar_pi.mapa_ui.navegador import Navegador  # noqa: E402
import h3  # noqa: E402

assert "compat" in h3.__file__


class Loc:
    def __init__(self, lat, lon, acc=8.0, speed=None, bearing=None, prov="gps"):
        self.a = (lat, lon, acc, speed, bearing, prov)

    def getProvider(self): return self.a[5]
    def getLatitude(self): return self.a[0]
    def getLongitude(self): return self.a[1]
    def hasAccuracy(self): return self.a[2] is not None
    def getAccuracy(self): return self.a[2]
    def hasSpeed(self): return self.a[3] is not None
    def getSpeed(self): return self.a[3]
    def hasBearing(self): return self.a[4] is not None
    def getBearing(self): return self.a[4]


class VozMuda:
    def falar(self, texto, t=None):
        return {"texto": texto}


rmod.FONTE = str(APP / "fonts/DejaVuSans.ttf")
rmod.FONTE_B = str(APP / "fonts/DejaVuSans-Bold.ttf")
pygame.init()
W, H = 1040, 480     # proporção ~ A35 em paisagem
tela = pygame.Surface((W, H))
malha = MalhaTiles(config.GRAPH_TILES, "sp")
rend = rmod.Renderer(MapaBase(malha), W, H, config.ZOOM_M_PX, True)
cc = sqlite3.connect(str(config.GRAPH_TILES))
f_peq = pygame.font.Font(rmod.FONTE, 15)


def cobertura(lat, lon):
    return cc.execute("SELECT 1 FROM aresta_cel WHERE cel=? LIMIT 1", (h3.latlng_to_cell(lat, lon, RES),)).fetchone() is not None


def rodar(nome, locs, pngs):
    import time as _t
    gps = plat.GPSAndroid()
    gps.cobertura = cobertura
    nav = Navegador(malha, db.conectar(), VozMuda())
    t = 0.0
    saida = {"vel_kmh": [], "voz": None}
    fora = False
    for k, loc in enumerate(locs):
        gps.t0 = _t.monotonic() - t
        gps._novo(loc)
        for f in gps.novos(t):
            nav.novo_fix(f, t)
            fora = not cobertura(f.lat, f.lon)
        for q in range(20):                     # 20 quadros por segundo
            e = nav.quadro(t + q * 0.05, 0.05)
            if e:
                rend.desenhar(tela, e["cx"], e["cy"], e["rumo"], e["vel_kmh"], e["radares"], e["ativo_id"], e["placa"],
                              e["banda"], e["via"], e["gps_ok"], e["limite_via"], e["estimado"], e["dist_alvo"], 0.05)
                if fora:
                    s = f_peq.render("fora da área do mapa", True, rmod.TEXTO_RUA)
                    tela.blit(s, ((W - s.get_width()) // 2, H - s.get_height() - 4))
        if e:
            saida["vel_kmh"].append(round(e["vel_kmh"]))
        if k in pngs:
            pygame.image.save(tela, str(AND / "resultados" / f"gps_{nome}_{k:02d}.png"))
        t += 1.0
    saida["voz"] = [(round(tv), r["texto"]) for tv, r in nav.eventos_voz]
    print(nome, json.dumps(saida, ensure_ascii=False))
    return saida


rnd = random.Random(3)
# A) parado na Av. Paulista: 1ª leitura com speed lixo (36,7 m/s, acc 80 m) + jitter de 4 m, hasSpeed com 0,3 m/s
la0, lo0 = -23.56140, -46.65590
par = [Loc(la0, lo0, acc=80.0, speed=36.7)] + [Loc(la0 + rnd.gauss(0, 4e-5), lo0 + rnd.gauss(0, 4e-5), acc=6.0,
                                                speed=0.3 if k % 2 else None) for k in range(12)]
A = rodar("parado", par, {12})
# B) andando a 100 km/h numa BR-116 (BA) com radar DNIT: fixes do cenário br116_dnit como Location
d = json.loads((APP / "data/sim/br116_dnit.json").read_text())
andando = [Loc(x[1], x[2], acc=8.0, speed=x[3], bearing=x[4]) for x in d["fixes"]]
B = rodar("br116", andando, {10, 25, 30})
# C) salto de 3 km sem hasSpeed no meio de trecho andando
salto = andando[:8] + [Loc(andando[8].a[0] + 0.027, andando[8].a[1], acc=8.0)] + andando[9:14]
C = rodar("salto", salto, {8, 9})
# D) fora da área (Amazonas, sem malha)
fora = [Loc(-5.0 + k * 1e-4, -62.0, acc=10.0, speed=15.0, bearing=0.0) for k in range(6)]
D = rodar("fora", fora, {5})
assert max(A["vel_kmh"]) == 0, A
assert any("radar" in t for _, t in B["voz"]), B
assert max(C["vel_kmh"]) < 200, C
print("OK")
