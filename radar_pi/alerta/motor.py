"""Motor de alerta: radar à frente, na mesma via e sentido, distância ao longo da via.

v2: E5 histerese (começa a <=450 m, só termina acima de 480 m) e "segura" de 3 s quando o alvo some
por um quadro; voz (novo=True) uma vez por radar. E2/N6: vínculo de confiança baixa => via_incerta.
E11: limite "90/60" escolhe leve/pesado por RADAR_VEICULO. E3: segue avisando com posição estimada."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from radar_pi import config
from radar_pi.dados import db as dbmod
from radar_pi.mapa.grafo import MalhaOps, to_latlon
from radar_pi.mapmatch.hmm import Estado, Fix

CONF_MIN = 0.5


@dataclass
class Alerta:
    radar_id: str
    distancia_m: float
    limite_kmh: Optional[int]
    limite_texto: str
    limite_estimado: bool
    velocidade_kmh: float
    acima: bool
    via: str
    via_incerta: bool
    novo: bool
    fonte: str
    tipo: str = ""
    posicao_estimada: bool = False
    limite_composto: str = ""
    motivo_incerta: str = ""    # hmm | pista_colada | vinculo_baixa_conf   # E11: texto original da fonte, ex. "90/60" (leves/pesados)


def limite_para_veiculo(r: dict, veiculo: str = None):
    """E11: CET publica "90/60" = leves/pesados (NT 240 da CET). '60/50/50' segue sem significado
    confirmado: usa o 1º para leve e o menor para pesado (mais conservador)."""
    veiculo = (veiculo or config.VEICULO)
    txt = r.get("limite_texto") or ""
    nums = [int(n) for n in re.findall(r"\d+", txt)] if "/" in txt else []
    if not nums:
        return r.get("limite_kmh")
    return nums[0] if veiculo != "pesado" else min(nums)


class RepositorioRadares:
    """Carrega do SQLite, por anel H3, os radares vinculados às arestas da malha."""

    def __init__(self, con, malha: MalhaOps, k_anel=4):
        self.con, self.m, self.k = con, malha, k_anel
        self.cel_atual = None
        self.por_aresta = {}
        self.consultas = 0

    def _carregar(self, lat, lon):
        rads = dbmod.radares_proximos(self.con, lat, lon, k=self.k)
        self.consultas += 1
        por = {}
        if rads:
            ids = [r["id"] for r in rads]
            rmap = {r["id"]: r for r in rads}
            q = f"SELECT * FROM vinculos WHERE grafo=? AND radar_id IN ({','.join('?' * len(ids))})"
            for v in self.con.execute(q, [self.m.nome, *ids]):
                i = self.m.idx_of.get((v["u"], v["v"], v["k"]))
                if i is not None:
                    r = dict(rmap[v["radar_id"]])
                    r["_conf"] = v["confianca"]
                    r["_rumo"] = v["rumo"] if "rumo" in v.keys() else None
                    r["_metodo"] = v["metodo"]
                    por.setdefault(i, []).append((v["offset_m"], r))
        self.por_aresta = por

    def na_aresta(self, i, lat=None, lon=None):
        if lat is not None:
            c = dbmod.celula(lat, lon)
            if c != self.cel_atual:
                self.cel_atual = c
                self._carregar(lat, lon)
        return self.por_aresta.get(i, [])

    def todos(self):
        for i, lst in self.por_aresta.items():
            for off, r in lst:
                yield i, off, r


class MotorAlerta:
    def __init__(self, malha: MalhaOps, repo: RepositorioRadares, alerta_m=None, horizonte_m=None,
                 hist_m=None, segura_s=None):
        self.m, self.repo = malha, repo
        self.alerta_m = alerta_m or config.ALERTA_M
        self.horizonte_m = horizonte_m or config.HORIZONTE_M
        self.hist_m = config.ALERTA_HIST_M if hist_m is None else hist_m
        self.segura_s = config.ALERTA_SEGURA_S if segura_s is None else segura_s
        self.ativo: Optional[str] = None
        self.passados = {}   # radar_id -> t
        self.anunciados = {}  # radar_id -> t (voz só 1x)
        self.ultima_dist = None
        self.ultimo_alerta: Optional[Alerta] = None
        self.t_ultimo = None
        self._ultimo_do_alt = False
        self.ambig_seq = 0   # E5: quadros seguidos de ambiguidade (debounce do ramo alternativo)

    def _a_frente(self, i, offset, lat, lon):
        """Percorre a mesma via a partir de (i, offset) até o horizonte."""
        achados, acum, j, off, visit = [], 0.0, i, offset, set()
        while j is not None and acum <= self.horizonte_m + self.hist_m and j not in visit:
            visit.add(j)
            for r_off, r in self.repo.na_aresta(j, lat, lon):
                if r_off >= off:
                    achados.append((acum + r_off - off, r, j))
            acum += self.m.attr[j]["len"] - off
            j, off = self.m.continuacao_mesma_via(j), 0.0
        achados.sort(key=lambda t: t[0])
        return achados

    def _pista_colada(self, est, fix):
        """E4: pista paralela, mesmo sentido, mais perto que 2,5x o erro do GPS (máx. 30 m) => o GPS não separa as
        pistas; o aviso sai como 'via incerta' mesmo que o HMM esteja confiante."""
        sig = fix.precisao_m if fix.valido and fix.precisao_m else 10.0
        lim = min(30.0, 2.5 * sig)
        if lim < 5:
            return None
        x, y = self.m.ponto(est.i, est.offset)
        r0 = self.m.rumo(est.i, est.offset)
        nome = self.m.attr[est.i]["nome"]
        for j, d, off in self.m.candidatos(x, y, lim, 12):
            b = self.m.attr[j]
            hw = b["highway"] or ""
            # alças (_link), vias sem nome e ruas locais não são "pista paralela"
            if j == est.i or d < 3.0 or b["nome"] == nome or not b["nome"] or hw.endswith("_link") or \
                    hw not in ("motorway", "trunk", "primary"):
                continue
            if abs((self.m.rumo(j, off) - r0 + 180) % 360 - 180) <= 20:
                return j, off
        return None

    def _marcar_passado(self, t):
        if self.ativo and self.ultima_dist is not None and self.ultima_dist < 60:
            self.passados[self.ativo] = t

    def passo(self, est: Estado, fix: Fix) -> Optional[Alerta]:
        if est.i is None or not self.m.tem(est.i):
            return None
        if fix.valido:
            lat, lon = fix.lat, fix.lon
        else:
            lat, lon = to_latlon(*self.m.ponto(est.i, est.offset))
        achados = self._a_frente(est.i, est.offset, lat, lon)
        # E4: "via incerta" só quando as duas hipóteses do HMM discordam sobre ESTE radar
        ids_main, ids_alt = {a[1]["id"] for a in achados}, set()
        motivo = ""
        self.ambig_seq = self.ambig_seq + 1 if est.ambiguo else 0
        duvida = est.ambiguo and self.ambig_seq >= 2 and est.alt_i is not None and self.m.tem(est.alt_i)
        if duvida:
            alt = self._a_frente(est.alt_i, est.alt_offset, lat, lon)
            ids_alt = {a[1]["id"] for a in alt}
            motivo = "hmm"
            achados = sorted(achados + [a for a in alt if a[1]["id"] not in ids_main], key=lambda t: t[0])
        else:
            par = self._pista_colada(est, fix)
            if par is not None:
                alt = self._a_frente(par[0], par[1], lat, lon)
                ids_alt = {a[1]["id"] for a in alt}
                # só importa se as pistas discordam sobre os radares à frente
                if ids_alt != ids_main:
                    duvida, motivo = True, "pista_colada"
                    achados = sorted(achados + [a for a in alt if a[1]["id"] not in ids_main], key=lambda t: t[0])
        achados = [a for a in achados if a[1]["id"] not in self.passados or fix.t - self.passados[a[1]["id"]] > 90]
        # E5: o radar já ativo continua até ALERTA_M + histerese; um novo só entra a <= ALERTA_M
        alvo = None
        if self.ativo:
            alvo = next((a for a in achados if a[1]["id"] == self.ativo and a[0] <= self.alerta_m + self.hist_m), None)
            prox = next((a for a in achados if a[0] <= self.alerta_m), None)
            if prox and alvo and prox[0] < alvo[0] - 5:
                alvo = prox
        if alvo is None:
            alvo = next((a for a in achados if a[0] <= self.alerta_m), None)
        v = fix.vel_ms * 3.6
        if alvo is None:
            # E5: segura o aviso por alguns segundos (alvo sumiu por ruído), com distância extrapolada
            if self.ultimo_alerta and not self._ultimo_do_alt and self.t_ultimo is not None and fix.t - self.t_ultimo <= self.segura_s \
                    and self.ultima_dist is not None and self.ultima_dist > 60:
                a = self.ultimo_alerta
                d = max(0.0, a.distancia_m - fix.vel_ms * (fix.t - self.t_ultimo))
                lim = a.limite_kmh
                return Alerta(a.radar_id, d, lim, a.limite_texto, a.limite_estimado, v, bool(lim and v > lim),
                              a.via, a.via_incerta, False, a.fonte, a.tipo, not fix.valido, a.limite_composto, a.motivo_incerta)
            self._marcar_passado(fix.t)
            self.ativo, self.ultima_dist, self.ultimo_alerta = None, None, None
            return None
        dist, r, j = alvo
        if r["id"] != self.ativo:
            self._marcar_passado(fix.t)
        novo = r["id"] not in self.anunciados or fix.t - self.anunciados[r["id"]] > 120
        if novo:
            self.anunciados[r["id"]] = fix.t
        self.ativo, self.ultima_dist = r["id"], dist
        lim, estimado = limite_para_veiculo(r), False
        if lim is None and self.m.attr[j]["maxspeed"]:
            lim, estimado = self.m.attr[j]["maxspeed"], True
        txt = str(lim) if lim else "?"
        nome = self.m.attr[est.i]["nome_orig"]
        nome = nome[0] if isinstance(nome, list) else nome
        baixa = (r.get("_conf") or 1.0) < CONF_MIN
        inc = (duvida and not (r["id"] in ids_main and r["id"] in ids_alt))
        motivo = motivo if inc else ("vinculo_baixa_conf" if baixa else "")
        al = Alerta(r["id"], dist, lim, txt + ("*" if estimado else ""), estimado, v, bool(lim and v > lim),
                    nome, inc or baixa, novo, r["fonte"], r.get("tipo") or "", not fix.valido,
                    (r.get("limite_texto") or "") if "/" in (r.get("limite_texto") or "") else "", motivo)
        self.ultimo_alerta, self.t_ultimo = al, fix.t
        self._ultimo_do_alt = r["id"] not in ids_main
        return al
