"""Telemetria LOCAL e anonimizada (LGPD) — esqueleto.

O que é gravado (data/telemetria.sqlite, nunca sai do aparelho):
  por ponto (célula H3 res 11, ~25 m) + sentido (8 setores de 45°): nº de passagens, soma/soma² da
  velocidade mínima no ponto, soma da queda de velocidade em relação aos 150–300 m anteriores e nº de
  passagens com desaceleração forte. Só contadores somados.
O que NÃO é gravado: trajeto, sequência de pontos, horário/data, id do aparelho/usuário, coordenadas cruas.
Proteções extras:
  - os primeiros e os últimos `corte_m` (500 m) de cada viagem são descartados (origem/destino = casa/trabalho);
  - o histórico recente fica só em memória (buffer de ~300 m) e é apagado ao fechar;
  - análise só considera pontos com >= k passagens (k-anonimato, RADAR_TELEMETRIA_K_MIN).
Sinais gerados (tabela telemetria_sinais; NUNCA alteram a tabela radares):
  candidato  - ponto sem radar na base onde a maioria desacelera forte;
  suspeito   - radar ativo onde quase ninguém desacelera e a velocidade média passa do limite.
"""
from __future__ import annotations

import math
import sqlite3
from collections import deque
from pathlib import Path

import h3

from radar_pi.validacao.osm import haversine

RES = 11
SCHEMA = """
CREATE TABLE IF NOT EXISTS telemetria_ponto(
  h3 TEXT, setor INTEGER, n INTEGER, soma_v REAL, soma_v2 REAL, soma_queda REAL, n_desacel INTEGER,
  PRIMARY KEY(h3, setor));
CREATE TABLE IF NOT EXISTS telemetria_sinais(
  tipo TEXT, h3 TEXT, setor INTEGER, lat REAL, lon REAL, n INTEGER, vel_media REAL, queda_media REAL,
  frac_desacel REAL, radar_id TEXT, limite INTEGER);
"""


def setor(rumo):
    return None if rumo is None else int(((rumo % 360) + 22.5) // 45) % 8


class Telemetria:
    def __init__(self, path: Path | str, corte_m=500.0, janela=(150.0, 300.0), queda_forte_kmh=15.0,
                 queda_forte_rel=0.2, k_min=5):
        self.con = sqlite3.connect(str(path))
        self.con.executescript(SCHEMA)
        self.corte_m, self.janela = corte_m, janela
        self.queda_forte_kmh, self.queda_forte_rel, self.k_min = queda_forte_kmh, queda_forte_rel, k_min
        self._nova_viagem()

    # ---------------------------------------------------------------- coleta
    def _nova_viagem(self):
        self.hist = deque()          # (dist_acum, vel_kmh) — só ~300 m, em memória
        self.dist = 0.0
        self.ult = None
        self.cel = None              # passagem corrente: [h3, setor, v_min, v_ref, dist_entrada]
        self.pendentes = deque()     # passagens fechadas aguardando sair dos últimos corte_m

    def alimentar(self, lat, lon, vel_kmh, rumo=None):
        if lat is None or lon is None:
            return
        if self.ult:
            d = haversine(self.ult[0], self.ult[1], lat, lon)
            if d > 500:              # salto de GPS / túnel longo: começa outra "viagem" (não liga os pontos)
                self.fim_viagem()
                return self.alimentar(lat, lon, vel_kmh, rumo)
            self.dist += d
        self.ult = (lat, lon)
        self.hist.append((self.dist, vel_kmh))
        while self.hist and self.dist - self.hist[0][0] > self.janela[1]:
            self.hist.popleft()
        c, s = h3.latlng_to_cell(lat, lon, RES), setor(rumo)
        if self.cel and (self.cel[0], self.cel[1]) == (c, s):
            self.cel[2] = min(self.cel[2], vel_kmh)
            return
        if self.cel:
            self.pendentes.append(tuple(self.cel))
        ref = [v for dd, v in self.hist if self.janela[0] <= self.dist - dd <= self.janela[1]]
        self.cel = [c, s, vel_kmh, (sum(ref) / len(ref)) if ref else None, self.dist]
        # só grava passagens que já estão a > corte_m do fim atual (o fim real pode ser o destino)
        while self.pendentes and self.dist - self.pendentes[0][4] > self.corte_m:
            self._gravar(self.pendentes.popleft())

    def _gravar(self, p):
        h, s, vmin, vref, d_ent = p
        if d_ent < self.corte_m or s is None:      # início da viagem (origem) ou sem rumo: descarta
            return
        queda = max(0.0, vref - vmin) if vref is not None else 0.0
        forte = int(vref is not None and queda >= self.queda_forte_kmh and queda >= self.queda_forte_rel * vref)
        self.con.execute("INSERT INTO telemetria_ponto VALUES(?,?,1,?,?,?,?) ON CONFLICT(h3,setor) DO UPDATE SET "
                         "n=n+1, soma_v=soma_v+excluded.soma_v, soma_v2=soma_v2+excluded.soma_v2, "
                         "soma_queda=soma_queda+excluded.soma_queda, n_desacel=n_desacel+excluded.n_desacel",
                         (h, s, vmin, vmin * vmin, queda, forte))

    def fim_viagem(self):
        """Descarta as passagens dos últimos corte_m (destino) e limpa a memória."""
        self.con.commit()
        self._nova_viagem()

    def fechar(self):
        self.fim_viagem()
        self.con.close()

    # ---------------------------------------------------------------- análise
    def pontos(self):
        return [dict(zip(("h3", "setor", "n", "vel_media", "vel_dp", "queda_media", "frac_desacel"),
                         (h, s, n, sv / n, math.sqrt(max(0.0, sv2 / n - (sv / n) ** 2)), sq / n, nd / n)))
                for h, s, n, sv, sv2, sq, nd in self.con.execute("SELECT * FROM telemetria_ponto WHERE n>=?",
                                                                 (self.k_min,))]

    def analisar(self, con_radares, raio_sem_radar=80.0, raio_radar=30.0, frac_cand=0.6, queda_cand=15.0,
                 frac_susp=0.2, folga_susp=1.1):
        """Gera candidatos/suspeitos. Não mexe na tabela radares (só lê)."""
        pts = self.pontos()
        # agrupa células vizinhas no mesmo sentido: fica a de maior queda (evita 3 candidatos por ponto)
        pts.sort(key=lambda p: -p["queda_media"])
        sinais, usados = [], set()
        for p in pts:
            lat, lon = h3.cell_to_latlng(p["h3"])
            rads = [r for r in con_radares.execute(
                "SELECT id, lat, lon, limite_kmh, status FROM radares WHERE lat BETWEEN ? AND ? AND lon BETWEEN ? AND ?",
                (lat - 0.002, lat + 0.002, lon - 0.002, lon + 0.002))]
            perto = [(haversine(lat, lon, r[1], r[2]), r) for r in rads]
            if (p["frac_desacel"] >= frac_cand and p["queda_media"] >= queda_cand
                    and not any(d <= raio_sem_radar for d, _ in perto)):
                if any(h3.grid_distance(p["h3"], u) <= 3 for u in usados):
                    continue
                usados.add(p["h3"])
                sinais.append(dict(p, tipo="candidato", lat=lat, lon=lon, radar_id=None, limite=None))
            ativos = [(d, r) for d, r in perto if d <= raio_radar and r[4] == "ativo" and r[3]]
            if ativos and p["frac_desacel"] <= frac_susp:
                d, r = min(ativos)
                if p["vel_media"] > folga_susp * r[3]:
                    sinais.append(dict(p, tipo="suspeito", lat=lat, lon=lon, radar_id=r[0], limite=r[3]))
        # suspeito: 1 por radar (a célula mais rápida)
        vist, out = set(), []
        for s in sorted(sinais, key=lambda s: -s["vel_media"]):
            if s["tipo"] == "suspeito":
                if s["radar_id"] in vist:
                    continue
                vist.add(s["radar_id"])
            out.append(s)
        self.con.execute("DELETE FROM telemetria_sinais")
        self.con.executemany("INSERT INTO telemetria_sinais VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                             [(s["tipo"], s["h3"], s["setor"], s["lat"], s["lon"], s["n"], round(s["vel_media"], 1),
                               round(s["queda_media"], 1), round(s["frac_desacel"], 2), s["radar_id"], s["limite"])
                              for s in out])
        self.con.commit()
        return out
