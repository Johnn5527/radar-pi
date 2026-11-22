"""Map matching online por HMM (Newson & Krumm 2009), adaptado para tempo real (filtro de Viterbi
sem suavização para trás). Estados = arestas dirigidas candidatas.

v2: E3 dead reckoning pela malha quando o GPS some (túnel); E4 ambiguidade medida pela probabilidade
somada por VIA (não por aresta isolada), penalidade de troca de via e histerese na via escolhida."""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

from radar_pi.mapa.grafo import MalhaOps, to_xy


@dataclass
class Fix:
    t: float
    lat: Optional[float]
    lon: Optional[float]
    vel_ms: float = 0.0
    rumo: Optional[float] = None
    precisao_m: float = 10.0

    @property
    def valido(self):
        return self.lat is not None


@dataclass
class Estado:
    i: Optional[int] = None
    offset: float = 0.0
    prob: float = 0.0
    alt_i: Optional[int] = None
    alt_offset: float = 0.0
    alt_prob: float = 0.0
    ambiguo: bool = False
    sem_gps: bool = False
    fora_da_via: bool = False
    quebras: int = 0
    seg_sem_gps: float = 0.0
    estimado: bool = False      # E3: posição por dead reckoning
    dr_m: float = 0.0           # metros percorridos por dead reckoning


def _ang(a, b):
    return abs((a - b + 180) % 360 - 180)


class MapMatcherHMM:
    def __init__(self, malha: MalhaOps, sigma_z=10.0, beta=8.0, sigma_rumo=25.0, raio_min=35.0, max_cand=10,
                 pen_troca=1.5, limiar_ambig=0.8, histerese_lp=1.0, dr_max_s=90.0, dr_max_m=2500.0,
                 esquecimento=1.0, pen_salto=None):
        self.m = malha
        self.sigma_z, self.beta, self.sigma_rumo = sigma_z, beta, sigma_rumo
        self.raio_min, self.max_cand = raio_min, max_cand
        self.pen_troca, self.limiar_ambig, self.histerese_lp = pen_troca, limiar_ambig, histerese_lp
        self.dr_max_s, self.dr_max_m = dr_max_s, dr_max_m
        # E4: o erro do GPS é correlacionado no tempo; sem esquecimento o HMM "trava" 100% numa pista
        # paralela escolhida no começo. lambda<1 limita a confiança à evidência dos últimos segundos, e o
        # salto (com penalidade) deixa recuperar de uma pista errada sem ligação no grafo.
        self.esquecimento, self.pen_salto = esquecimento, pen_salto
        self.prev = {}   # i -> (logp, offset)
        self.prev_xy = None
        self.prev_t = None
        self.ult_vel = 0.0
        self.estado = Estado()

    def _emissao(self, i, d, off, fix, sigma):
        lp = -0.5 * (d / sigma) ** 2
        if fix.rumo is not None and fix.vel_ms >= 3.0:
            a = _ang(self.m.rumo(i, off), fix.rumo)
            lp += -0.5 * (a / self.sigma_rumo) ** 2
        return lp

    def _transicao(self, pi, poff, ci, coff, d_gc, dt):
        if pi == ci:
            if coff >= poff - 2.5 * self.sigma_z:
                rota = abs(coff - poff)
            else:
                return -math.inf
            pen = 0.0
        else:
            rota = self.m.dist_rota(pi, poff, ci, coff, cutoff=max(300.0, d_gc * 2 + 60.0 * dt))
            if math.isinf(rota):
                return -math.inf
            # E4: trocar de via (nome diferente) custa caro; seguir a mesma via não
            pen = 0.0 if self.m.attr[pi]["nome"] == self.m.attr[ci]["nome"] else self.pen_troca
        return -abs(rota - d_gc) / self.beta - pen

    # --- E3: dead reckoning ---------------------------------------------------------
    def _dead_reckoning(self, fix: Fix):
        e = self.estado
        dt = (fix.t - self.prev_t) if self.prev_t is not None else 1.0
        dt = max(0.0, min(dt, 5.0))
        self.prev_t = fix.t
        if e.i is None or not self.m.tem(e.i) or e.seg_sem_gps > self.dr_max_s or e.dr_m > self.dr_max_m:
            e.estimado = False
            return e
        v = fix.vel_ms if fix.vel_ms and fix.vel_ms > 0.5 else self.ult_vel
        passo = v * dt
        i, off = e.i, e.offset + passo
        while off > self.m.attr[i]["len"]:
            j = self.m.continuacao_mesma_via(i)
            if j is None or not self.m.tem(j):
                off = self.m.attr[i]["len"]
                break
            off -= self.m.attr[i]["len"]
            i = j
        e.i, e.offset, e.estimado = i, off, True
        e.dr_m += passo
        e.alt_i, e.ambiguo = None, False
        # o HMM recomeça daqui quando o GPS voltar
        self.prev = {i: (0.0, off)}
        self.prev_xy = self.m.ponto(i, off)
        return e

    def atualizar(self, fix: Fix) -> Estado:
        e = self.estado
        if not fix.valido:
            e.sem_gps = True
            e.seg_sem_gps += (fix.t - self.prev_t) if self.prev_t is not None else 1.0
            return self._dead_reckoning(fix)
        x, y = to_xy(fix.lat, fix.lon)
        sigma = max(self.sigma_z, fix.precisao_m or 0)
        cands = self.m.candidatos(x, y, max(self.raio_min, 3.5 * sigma), self.max_cand)
        # malha por tiles pode ter descarregado arestas antigas
        self.prev = {k: v for k, v in self.prev.items() if self.m.tem(k)}
        e.sem_gps, e.seg_sem_gps, e.estimado, e.dr_m = False, 0.0, False, 0.0
        if fix.vel_ms and fix.vel_ms > 0.5:
            self.ult_vel = fix.vel_ms
        if not cands:
            e.fora_da_via = True
            self.prev = {}
            self.prev_xy, self.prev_t = (x, y), fix.t
            return e
        e.fora_da_via = False
        novo = {}
        dt = (fix.t - self.prev_t) if self.prev_t is not None else 1.0
        d_gc = math.hypot(x - self.prev_xy[0], y - self.prev_xy[1]) if self.prev_xy else 0.0
        for ci, d, coff in cands:
            em = self._emissao(ci, d, coff, fix, sigma)
            if not self.prev:
                novo[ci] = (em, coff)
                continue
            best = -math.inf
            for pi, (plp, poff) in self.prev.items():
                lp = self.esquecimento * plp + self._transicao(pi, poff, ci, coff, d_gc, dt)
                if lp > best:
                    best = lp
            if best == -math.inf and d < 30.0 and self.pen_salto is not None:
                # pista paralela colada sem ligação: entra como "salto" penalizado
                best = -self.pen_salto
            if best > -math.inf:
                novo[ci] = (best + em, coff)
        if not novo:  # quebra do HMM: nenhuma transição possível -> reinicia
            e.quebras += 1
            novo = {ci: (self._emissao(ci, d, coff, fix, sigma), coff) for ci, d, coff in cands}
        mx = max(v[0] for v in novo.values())
        self.prev = {k: (v[0] - mx, v[1]) for k, v in novo.items() if v[0] - mx > -60}
        tot = sum(math.exp(v[0]) for v in self.prev.values())
        ordem = sorted(self.prev.items(), key=lambda kv: -kv[1][0])
        (bi, (blp, boff)) = ordem[0]
        # E4/N8: histerese — se a via anterior ainda está quase empatada, fica nela
        if e.i is not None and self.m.tem(e.i):
            for ai, (alp, aoff) in ordem[1:]:
                if blp - alp > self.histerese_lp:
                    break
                if self.m.mesma_via(ai, aoff, e.i, e.offset) and not self.m.mesma_via(bi, boff, e.i, e.offset):
                    bi, blp, boff = ai, alp, aoff
                    break
        # E4: probabilidade agregada por via (mesmo nome e sentido)
        p_via = sum(math.exp(lp) for k, (lp, o) in self.prev.items() if self.m.mesma_via(k, o, bi, boff)) / tot
        e.i, e.offset, e.prob = bi, boff, p_via
        e.alt_i, e.alt_prob, e.ambiguo = None, 0.0, False
        for ai, (alp, aoff) in ordem:
            if not self.m.mesma_via(ai, aoff, bi, boff):
                e.alt_i, e.alt_offset = ai, aoff
                e.alt_prob = sum(math.exp(lp) for k, (lp, o) in self.prev.items() if self.m.mesma_via(k, o, ai, aoff)) / tot
                break
        e.ambiguo = e.alt_i is not None and e.prob < self.limiar_ambig and e.alt_prob > 0.15
        self.prev_xy, self.prev_t = (x, y), fix.t
        return e
