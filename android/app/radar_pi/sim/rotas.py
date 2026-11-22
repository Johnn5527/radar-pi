"""Gera trajetos GPS sintéticos (1 Hz) ao longo de arestas da malha, com ruído realista."""
from __future__ import annotations

import math
import random

from shapely.geometry import LineString
from shapely.ops import linemerge

from radar_pi.mapa.grafo import Malha, to_latlon
from radar_pi.mapmatch.hmm import Fix


def caminho_mesma_via(m: Malha, i0: int, comprimento_m: float):
    m.garantir(*m.ponto(i0, 0))
    seq, acum, j = [], 0.0, i0
    while j is not None and acum < comprimento_m and j not in seq:
        seq.append(j)
        acum += m.attr[j]["len"]
        j = m.continuacao_mesma_via(j)
    return seq


def caminho_por_nos(m: Malha, nos):
    seq = []
    for a, b in zip(nos, nos[1:]):
        seq.append(m.R[a][b]["e"])
    return seq


def geometria(m: Malha, arestas):
    coords = []
    for i in arestas:
        c = list(m.geoms[i].coords)
        coords.extend(c if not coords else c[1:])
    return LineString(coords)


def gerar(m: Malha, arestas, vel_kmh=50.0, ruido=(5.0, 15.0), seed=1, inicio_m=0.0, fim_m=None,
          perda=None, rumo_ruido=5.0, vel_func=None, t0=0.0):
    """perda: lista de (de_m, ate_m) ao longo do trajeto sem sinal (túnel).
    Devolve (fixes, verdade) - verdade = lista de (s_m, x, y, aresta_idx)."""
    rnd = random.Random(seed)
    g = geometria(m, arestas)
    cum, lens = [], 0.0
    for i in arestas:
        cum.append((lens, i))
        lens += m.attr[i]["len"]
    fim = min(fim_m or g.length, g.length)
    s, t, fixes, verdade = inicio_m, t0, [], []
    sig_ar = rnd.uniform(*ruido)
    ex = ey = 0.0
    while s <= fim:
        v = (vel_func(s) if vel_func else vel_kmh) / 3.6
        p = g.interpolate(s)
        a, b = g.interpolate(max(0, s - 2)), g.interpolate(min(g.length, s + 2))
        rumo = (math.degrees(math.atan2(b.x - a.x, b.y - a.y)) + 360) % 360
        aresta = max((c for c in cum if c[0] <= s), key=lambda c: c[0])[1]
        verdade.append((s, p.x, p.y, aresta))
        # ruído correlacionado (multicaminho/urbano): AR(1)
        if rnd.random() < 0.05:
            sig_ar = rnd.uniform(*ruido)
        ex = 0.8 * ex + 0.6 * rnd.gauss(0, sig_ar)
        ey = 0.8 * ey + 0.6 * rnd.gauss(0, sig_ar)
        if perda and any(d <= s <= e for d, e in perda):
            fixes.append(Fix(t, None, None, 0.0, None, 99.0))
        else:
            lat, lon = to_latlon(p.x + ex, p.y + ey)
            fixes.append(Fix(t, lat, lon, max(0.0, v + rnd.gauss(0, 0.3)), (rumo + rnd.gauss(0, rumo_ruido)) % 360, sig_ar))
        s += v * 1.0
        t += 1.0
    return fixes, verdade


def caminho_ate_radar(m: Malha, i_radar: int, off_radar: float, antes_m=800.0, depois_m=150.0):
    """Trajeto pela MESMA via que passa pelo radar: volta `antes_m` e segue `depois_m`.
    Devolve (arestas, s_radar) - s_radar = posição do radar ao longo do trajeto."""
    m.garantir(*m.ponto(i_radar, off_radar))
    nome = m.attr[i_radar]["nome"]
    ref = m.attr[i_radar]["ref"]
    tras, acum, j = [], off_radar, i_radar
    while acum < antes_m:
        u = m.edges[j][0]
        prev = None
        best = 999
        r0 = m.rumo(j, 0)
        for p in m.R.predecessors(u):
            e = m.R[p][u]["e"]
            if not m.tem(e):
                continue
            if m.edges[e][0] == m.edges[j][1] or e in tras or e == i_radar:
                continue
            a = m.attr[e]
            if (nome and a["nome"] != nome) or (not nome and ref and a["ref"] != ref):
                continue
            ang = abs((m.rumo(e, a["len"]) - r0 + 180) % 360 - 180)
            if ang < best:
                prev, best = e, ang
        if prev is None or best > 75:
            break
        tras.insert(0, prev)
        acum += m.attr[prev]["len"]
        j = prev
    frente, acum2, j = [i_radar], m.attr[i_radar]["len"] - off_radar, i_radar
    while acum2 < depois_m:
        j = m.continuacao_mesma_via(j)
        if j is None or j in frente or j in tras:
            break
        frente.append(j)
        acum2 += m.attr[j]["len"]
    s_radar = sum(m.attr[e]["len"] for e in tras) + off_radar
    return tras + frente, s_radar
