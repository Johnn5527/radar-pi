"""Trajetos de simulação sobre a malha real (radares reais do SQLite)."""
from __future__ import annotations

import random

from radar_pi.sim.rotas import caminho_ate_radar, caminho_mesma_via, gerar

CENARIOS = {
    # nome: (malha, filtro SQL, perfil de velocidade (s_rel->km/h), metros antes, depois)
    "marginal": ("sp", "r.limite_texto='90/60' AND r.descricao LIKE '%TIETE%EXPRESSA%'",
                 lambda d: 95 if d < -330 else 80, 650, 120),
    "cet_acima": ("sp", "r.fonte='cet_sp' AND r.limite_kmh=50 AND v.metodo='sentido_texto' AND r.descricao LIKE 'AV%'",
                  lambda d: 68, 560, 100),
    "tamoios": ("sp", "r.via='SP-099' AND r.fonte='der_sp'", lambda d: 95, 700, 150),
    # vídeos com voz Piper (rotas diferentes da Marginal)
    "paulista": ("sp", "r.id='cet_sp:5393/5394#2024-05-30'", lambda d: 64 if d < -280 else 49, 520, 90),
    "radial_leste": ("sp", "r.id='cet_sp:4326#2024-07-30'", lambda d: 66 if d < -280 else 49, 520, 90),
    "tamoios_piper": ("sp", "r.via='SP-099' AND r.fonte='der_sp' AND r.limite_kmh IS NOT NULL", lambda d: 96 if d < -300 else 78, 580, 120),
}


def trajeto(nome, malha, con, seed=1, ruido=(5.0, 12.0)):
    if nome == "sem_radar":
        return _sem_radar(malha, con, seed, ruido)
    _, filtro, perfil, antes, depois = CENARIOS[nome]
    r = con.execute(f"SELECT r.id, v.u, v.v, v.k, v.offset_m FROM radares r JOIN vinculos v ON v.radar_id=r.id "
                    f"WHERE v.grafo=? AND r.status='ativo' AND {filtro} LIMIT 1", (malha.nome,)).fetchone()
    i = malha.idx_of[(r["u"], r["v"], r["k"])]
    ar, s = caminho_ate_radar(malha, i, r["offset_m"], antes + 200, depois + 50)
    ini = max(0.0, s - antes)
    fx, ver = gerar(malha, ar, seed=seed, inicio_m=ini, fim_m=s + depois, ruido=ruido,
                    vel_func=lambda sa: perfil(sa - s))
    return fx, {"radar": r["id"], "s_radar": s - ini}


def _sem_radar(malha, con, seed, ruido):
    """Via nomeada, secundária/terciária, sem radar vinculado a menos de 2 km pela via."""
    from radar_pi.mapa.grafo import to_xy
    com_radar = {(row[0], row[1], row[2]) for row in con.execute("SELECT u,v,k FROM vinculos WHERE grafo=?", (malha.nome,))}
    malha.garantir(*to_xy(-23.5503, -46.6339))   # tiles: região central carregada
    itens = malha.attr.items() if isinstance(malha.attr, dict) else enumerate(malha.attr)
    idx = sorted(i for i, a in itens if a["highway"] in ("secondary", "tertiary") and a["nome"])
    rnd = random.Random(seed)
    for _ in range(2000):
        i = rnd.choice(idx)
        ar = caminho_mesma_via(malha, i, 700)
        if sum(malha.attr[j]["len"] for j in ar) < 600:
            continue
        mais = caminho_mesma_via(malha, ar[-1], 2000)
        if any(malha.edges[j] in com_radar for j in ar + mais):
            continue
        fx, _ = gerar(malha, ar, vel_kmh=40, seed=seed, ruido=ruido)
        return fx, {"radar": None, "via": malha.attr[i]["nome_orig"]}
    raise RuntimeError("não achei via sem radar")
