"""Desempenho: tempo por ciclo, memória, tamanho do banco/malha. Estimativa para o Pi 4."""
import gc
import os
import random
import statistics as st
import time

import psutil

from radar_pi.dados import db
from radar_pi.sim.executor import rodar
from radar_pi.sim.rotas import caminho_ate_radar, gerar
from conftest import ROOT

FATOR_PI4 = (3.0, 5.0)  # premissa: 1 núcleo Cortex-A72 1,5-1,8 GHz ~3-5x mais lento que 1 núcleo deste box


def test_desempenho_sp(registrar):
    proc = psutil.Process()
    rss0 = proc.memory_info().rss
    t = time.time()
    from radar_pi.mapa.tiles import MalhaTiles
    m = MalhaTiles(ROOT / "data/osm/malha_sp.sqlite", "sp")   # N10: tiles sob demanda
    t_load = time.time() - t
    gc.collect()
    gc.freeze()   # E9: objetos de longa duração fora da coleta de lixo (o app faz o mesmo)
    rss_malha = proc.memory_info().rss - rss0
    con = db.conectar(ROOT / "data/radares.sqlite")
    vinc = [tuple(r) for r in con.execute(
        "SELECT v.u,v.v,v.k,v.offset_m FROM vinculos v JOIN radares r ON r.id=v.radar_id WHERE v.grafo='sp' AND r.status='ativo' AND r.municipio='SAO PAULO'")]
    random.Random(1).shuffle(vinc)
    ms, ms_cont, n_rotas = [], [], 0
    t_total = time.time()
    for n, (u, v, k, off) in enumerate(vinc[:60]):
        i = m.idx_of.get((u, v, k))
        if i is None:
            continue
        ar, s = caminho_ate_radar(m, i, off, 1500, 500)
        fx, _ = gerar(m, ar, vel_kmh=random.Random(n).choice([30, 50, 70]), seed=n)
        q, _, _ = rodar(m, con, fx)
        ms += [x.ms for x in q]
        ms_cont += [x.ms for x in q[3:]]   # sem os 3 primeiros ciclos (o teste "teletransporta" o carro p/ outra região)
        n_rotas += 1
    ms.sort()
    ms_cont.sort()
    mx_cont, p99_cont = ms_cont[-1], ms_cont[int(.99 * len(ms_cont))]
    p50, p95, p99, mx = st.median(ms), ms[int(.95 * len(ms))], ms[int(.99 * len(ms))], ms[-1]
    tam = {"radares.sqlite_MB": round(os.path.getsize(ROOT / "data/radares.sqlite") / 1e6, 2),
           "sp_city.graphml_MB": round(os.path.getsize(ROOT / "data/osm/sp_city.graphml") / 1e6, 1),
           "malha_sp.sqlite_MB": round(os.path.getsize(ROOT / "data/osm/malha_sp.sqlite") / 1e6, 1)}
    registrar(cenario="desempenho", ciclos=len(ms), rotas=n_rotas, ms_p50=round(p50, 2), ms_p95=round(p95, 2),
              ms_p99=round(p99, 2), ms_max=round(mx, 1), carga_malha_s=round(t_load, 1),
              rss_malha_MB=round(rss_malha / 1e6), rss_total_MB=round(proc.memory_info().rss / 1e6),
              tamanhos=tam, arestas_carregadas_fim=len(m.edges), cargas_tiles=m.cargas, pi4_p95_estimado_ms=[round(p95 * f, 1) for f in FATOR_PI4],
              pi4_max_estimado_ms=[round(mx * f) for f in FATOR_PI4], seg_total=round(time.time() - t_total, 1),
              continuo_ms_p99=round(p99_cont, 2), continuo_ms_max=round(mx_cont, 1),
              pi4_max_continuo_estimado_ms=[round(mx_cont * f) for f in FATOR_PI4])
    assert p95 * FATOR_PI4[1] < 200, "ciclo p95 estimado no Pi 4 acima de 200 ms"
    # pior ciclo dirigindo de forma contínua (a partida/teletransporte carrega 7 tiles de uma vez)
    assert mx_cont * FATOR_PI4[1] < 1000, "pior ciclo estimado no Pi 4 passa de 1 s (perde um fix de GPS)"
    assert proc.memory_info().rss / 1e6 < 1500, "memória acima de 1,5 GB (Pi 4 de 2 GB fica no limite)"
    gc.unfreeze()
