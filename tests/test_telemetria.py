"""Item 5: telemetria local anonimizada — candidato (desacelera sem radar) e suspeito (radar sem desaceleração)."""
import math
import random
import sqlite3

import h3

from radar_pi.dados import db
from radar_pi.telemetria.agregador import Telemetria

LAT0, LON0 = -23.60, -46.70
M_LON = 111_320 * math.cos(math.radians(LAT0))
S_RADAR, S_CAND, L = 1200.0, 2200.0, 3400.0


def ponto(s, ruido=0.0, rng=None):
    dn = rng.gauss(0, ruido) if rng else 0.0
    de = rng.gauss(0, ruido) if rng else 0.0
    return LAT0 + dn / 111_320, LON0 + (s + de) / M_LON


def viagem(tel, rng, desacelera_cand=True):
    """Via reta para o leste; radar fixo em S_RADAR (limite 60) onde ninguém freia; lombada/radar
    não cadastrado em S_CAND onde a maioria freia de 80 para ~45."""
    s = 0.0
    while s < L:
        v = 80.0
        if desacelera_cand:
            v -= 35 * max(0.0, 1 - abs(s - S_CAND) / 150)
        v += rng.gauss(0, 2)
        lat, lon = ponto(s, 3.0, rng)
        tel.alimentar(lat, lon, v, 90.0 + rng.gauss(0, 3))
        s += v / 3.6
    tel.fim_viagem()


def base_radares(tmp_path):
    con = db.conectar(tmp_path / "radares.sqlite")
    lat, lon = ponto(S_RADAR)
    db.gravar_radares(con, [{"id": "cet:R", "fonte": "cet_sp", "lat": lat, "lon": lon, "limite_kmh": 60, "status": "ativo"}])
    return con


def test_candidato_e_suspeito_com_simulacao(tmp_path):
    rng = random.Random(7)
    tel = Telemetria(tmp_path / "tel.sqlite", k_min=5)
    for i in range(25):
        viagem(tel, rng, desacelera_cand=(i % 5 != 0))      # 80% freia no ponto sem radar
    con = base_radares(tmp_path)
    antes = con.execute("SELECT * FROM radares").fetchall()
    sinais = tel.analisar(con)
    cand = [s for s in sinais if s["tipo"] == "candidato"]
    susp = [s for s in sinais if s["tipo"] == "suspeito"]
    assert len(cand) == 1 and len(susp) == 1, sinais
    d_cand = (cand[0]["lon"] - LON0) * M_LON
    assert abs(d_cand - S_CAND) < 80, d_cand
    assert susp[0]["radar_id"] == "cet:R" and susp[0]["vel_media"] > 66
    assert con.execute("SELECT * FROM radares").fetchall() == antes          # nunca altera/remove radar
    print("TELEMETRIA", {"candidato_m": round(d_cand), "queda_media": round(cand[0]["queda_media"], 1),
                         "frac_desacel": cand[0]["frac_desacel"], "suspeito_vel": round(susp[0]["vel_media"], 1),
                         "pontos": len(tel.pontos())})


def test_lgpd_sem_trajeto_sem_origem_destino_e_k_anonimato(tmp_path):
    rng = random.Random(1)
    path = tmp_path / "tel.sqlite"
    tel = Telemetria(path, k_min=5)
    for _ in range(3):
        viagem(tel, rng)
    con = base_radares(tmp_path)
    assert tel.analisar(con) == [] and tel.pontos() == []      # < k passagens: nada é analisado
    tel.fechar()
    raw = sqlite3.connect(path)
    cols = {r[1] for r in raw.execute("PRAGMA table_info(telemetria_ponto)")}
    assert cols == {"h3", "setor", "n", "soma_v", "soma_v2", "soma_queda", "n_desacel"}   # sem hora, id, sequência
    tabelas = {r[0] for r in raw.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert tabelas == {"telemetria_ponto", "telemetria_sinais"}
    for (c,) in raw.execute("SELECT h3 FROM telemetria_ponto"):
        lat, lon = h3.cell_to_latlng(c)
        s = (lon - LON0) * M_LON
        assert 450 < s < L - 400, s                         # 1os/últimos 500 m (origem/destino) nunca gravados
    assert raw.execute("SELECT max(n) FROM telemetria_ponto").fetchone()[0] <= 3


def test_salto_de_gps_nao_liga_pontos(tmp_path):
    tel = Telemetria(tmp_path / "t.sqlite", k_min=1)
    rng = random.Random(3)
    s = 0.0
    while s < 1500:
        tel.alimentar(*ponto(s), 60, 90)
        s += 60 / 3.6
    tel.alimentar(*ponto(50_000), 60, 90)                    # salto de 48 km: vira outra viagem
    assert tel.dist == 0.0 and len(tel.hist) == 1
    tel.fechar()
