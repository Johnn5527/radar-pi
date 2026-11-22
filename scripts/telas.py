"""Gera PNGs da tela simulada em cenários reais (malha OSM + radares oficiais)."""
import sqlite3

from radar_pi.dados import db
from radar_pi.sim.executor import rodar
from radar_pi.sim.rotas import caminho_ate_radar, gerar
from radar_pi.ui import tela
from radar_pi.voz.tts import Voz

con = db.conectar()


def cena(malha_nome, where, vel, saida, alvo_dist=250, seed=1):
    from radar_pi.mapa.tiles import MalhaTiles
    m = MalhaTiles("data/osm/malha_sp.sqlite", "sp")   # N10
    r = con.execute(f"SELECT r.id, v.u, v.v, v.k, v.offset_m FROM radares r JOIN vinculos v ON v.radar_id=r.id "
                    f"WHERE v.grafo=? AND r.status='ativo' AND {where} LIMIT 1", ("sp",)).fetchone()
    i = m.idx_of[(r["u"], r["v"], r["k"])]
    ar, s = caminho_ate_radar(m, i, r["offset_m"], 800, 100)
    fx, ver = gerar(m, ar, vel_kmh=vel, seed=seed, inicio_m=max(0, s - 600), fim_m=s + 50, ruido=(5, 8))
    voz = Voz()
    q, _, _ = rodar(m, con, fx, voz=voz)
    k = min((j for j, x in enumerate(q) if x.alerta), key=lambda j: abs(q[j].alerta.distancia_m - alvo_dist))
    x = q[k]
    tela.render(x.alerta, fx[k].vel_ms * 3.6, x.via, x.sem_gps, x.alerta.via_incerta, caminho=saida)
    print(saida, r["id"], round(x.alerta.distancia_m), x.alerta.limite_texto, round(x.alerta.velocidade_kmh), x.alerta.acima,
          [v["texto"] for v in voz.log])
    return m


cena("sp_city", "r.limite_texto='90/60' AND r.descricao LIKE '%TIETE%EXPRESSA%'", 82, "resultados/telas/1_marginal_tiete_90-60_abaixo.png")
cena("sp_city", "r.fonte='cet_sp' AND r.limite_kmh=50 AND v.metodo='sentido_texto' AND r.descricao LIKE 'AV%'", 68, "resultados/telas/2_cet_50_acima_vermelho.png", seed=3)
cena("tamoios", "r.via='SP-099' AND r.fonte='der_sp'", 95, "resultados/telas/3_tamoios_sem_limite_oficial.png")
