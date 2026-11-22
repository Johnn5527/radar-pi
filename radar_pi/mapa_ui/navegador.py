"""Integra GPS -> HMM -> motor de alerta -> voz, e entrega o estado suavizado para a tela.

v2: N1 radares com o rumo fiscalizado (vínculo); N3 auto-zoom; N6 placa ignora vínculo de baixa
confiança; N7 limite OSM da via ("60*") quando não há radar; N8 seta prevista AO LONGO da via e
troca de pista suavizada; E3 posição estimada no túnel."""
from __future__ import annotations

import math
from typing import Optional

from radar_pi import config
from radar_pi.alerta.motor import CONF_MIN, MotorAlerta, RepositorioRadares, limite_para_veiculo
from radar_pi.dados import db as dbmod
from radar_pi.mapa.grafo import MalhaOps, to_latlon, to_xy
from radar_pi.mapmatch.hmm import Fix, MapMatcherHMM
from radar_pi.voz.tts import Voz


def _dif(a, b):
    return (a - b + 180) % 360 - 180


class Navegador:
    def __init__(self, malha: MalhaOps, con, voz: Optional[Voz] = None, horizonte_placa=None):
        self.m, self.con, self.voz = malha, con, voz
        self.mm = MapMatcherHMM(malha)
        self.repo = RepositorioRadares(con, malha)
        self.motor = MotorAlerta(malha, self.repo)
        self.horizonte_placa = horizonte_placa or config.HORIZONTE_PLACA_M
        self.fix: Optional[Fix] = None
        self.t_fix = 0.0
        self.est_i, self.est_off = None, 0.0
        self.alvo_xy = None          # posição (no mapa) do último fix
        self.rumo_alvo = 0.0
        self.vel_ms = 0.0
        self.disp_xy = None          # posição exibida (suavizada/extrapolada)
        self.disp_rumo = 0.0
        self.alerta = None
        self.placa = None
        self.limite_via = None       # N7: maxspeed OSM da via atual
        self.via = ""
        self.gps_ok = False
        self.estimado = False
        self._cel = None
        self.radares = []
        self.eventos_voz = []
        self.correcoes = []          # N8: salto entre a posição prevista e a nova (m)

    # --- 1 Hz ------------------------------------------------------------------
    def novo_fix(self, f: Fix, t_relogio: float):
        prev_pred = self._prevista(t_relogio) if self.alvo_xy is not None else None
        self.fix, self.t_fix = f, t_relogio
        est = self.mm.atualizar(f)
        self.gps_ok = f.valido
        self.estimado = est.estimado
        self.alerta = self.motor.passo(est, f)
        if self.alerta and self.alerta.novo and self.voz:
            reg = self.voz.falar(Voz.frase(self.alerta.limite_kmh), t=t_relogio)
            self.eventos_voz.append((t_relogio, reg))
        self.placa = self._placa(est, f)
        if f.valido and f.vel_ms is not None:
            self.vel_ms = f.vel_ms
        if est.i is not None and not est.fora_da_via and self.m.tem(est.i) and (f.valido or est.estimado):
            self.est_i, self.est_off = est.i, est.offset
            self.alvo_xy = self.m.ponto(est.i, est.offset)
            self.rumo_alvo = self.m.rumo(est.i, est.offset)
            a = self.m.attr[est.i]
            nome = a["nome_orig"]
            self.via = (nome[0] if isinstance(nome, list) else nome) or a["ref"] or ""
            self.limite_via = a["maxspeed"]
        elif f.valido:
            self.est_i = None
            self.alvo_xy = to_xy(f.lat, f.lon)
            self.limite_via = None
            if f.rumo is not None and f.vel_ms > 2:
                self.rumo_alvo = f.rumo
        if prev_pred is not None and self.alvo_xy is not None:
            self.correcoes.append(math.hypot(self.alvo_xy[0] - prev_pred[0], self.alvo_xy[1] - prev_pred[1]))
        if self.disp_xy is None and self.alvo_xy:
            self.disp_xy, self.disp_rumo = self.alvo_xy, self.rumo_alvo
        self._atualiza_radares()

    def _placa(self, est, f):
        if est.i is None or not self.m.tem(est.i):
            return None
        h0 = self.motor.horizonte_m
        self.motor.horizonte_m = self.horizonte_placa
        try:
            lat, lon = (f.lat, f.lon) if f.valido else to_latlon(*self.m.ponto(est.i, est.offset))
            achados = self.motor._a_frente(est.i, est.offset, lat, lon)
        finally:
            self.motor.horizonte_m = h0
        # N6: radar com vínculo de baixa confiança (sentido não resolvido) não vira placa
        achados = [a for a in achados if (a[1].get("_conf") or 1.0) >= CONF_MIN]
        if not achados:
            return None
        dist, r, j = achados[0]
        lim, estimado = limite_para_veiculo(r), False
        if lim is None and self.m.attr[j]["maxspeed"]:
            lim, estimado = self.m.attr[j]["maxspeed"], True
        txt = (str(lim) if lim else "?") + ("*" if estimado else "")
        return {"id": r["id"], "dist": dist, "limite": lim, "texto": txt, "estimado": estimado}

    def _atualiza_radares(self):
        if self.disp_xy is None:
            return
        lat, lon = to_latlon(*self.disp_xy)
        c = dbmod.celula(lat, lon)
        if c != self._cel:
            self._cel = c
            rs = dbmod.radares_proximos(self.con, lat, lon, k=8)
            if rs:
                ids = [r["id"] for r in rs]
                vinc = {}
                q = f"SELECT radar_id, rumo, confianca FROM vinculos WHERE grafo=? AND radar_id IN ({','.join('?' * len(ids))})"
                for v in self.con.execute(q, [self.m.nome, *ids]):
                    vinc.setdefault(v["radar_id"], []).append((v["rumo"], v["confianca"]))
                for r in rs:
                    r["_x"], r["_y"] = to_xy(r["lat"], r["lon"])
                    vs = vinc.get(r["id"], [])
                    # N1: rumo fiscalizado só quando o vínculo é único e confiável
                    r["_rumo"] = vs[0][0] if len(vs) == 1 and vs[0][1] >= CONF_MIN and vs[0][0] is not None else None
                    r["_conf"] = max((c for _, c in vs), default=0.0)
                    r["_lim"] = limite_para_veiculo(r)
            self.radares = rs

    # --- previsão entre fixes ----------------------------------------------------
    def _prevista(self, t_relogio):
        dtf = min(max(0.0, t_relogio - self.t_fix), 2.0) if (self.gps_ok or self.estimado) else 0.0
        if self.est_i is not None and self.m.tem(self.est_i):
            # N8: anda pela VIA (curvas), não em linha reta
            i, off = self.m.avancar(self.est_i, self.est_off, self.vel_ms * dtf)
            return self.m.ponto(i, off) + (self.m.rumo(i, off),)
        a = math.radians(self.rumo_alvo)
        return (self.alvo_xy[0] + math.sin(a) * self.vel_ms * dtf, self.alvo_xy[1] + math.cos(a) * self.vel_ms * dtf,
                self.rumo_alvo)

    # --- a cada quadro ----------------------------------------------------------
    def quadro(self, t_relogio: float, dt: float):
        """Extrapola a posição entre fixes (sem conhecer o futuro) e suaviza."""
        if self.alvo_xy is None:
            return None
        dtf = min(max(0.0, t_relogio - self.t_fix), 2.0) if (self.gps_ok or self.estimado) else 0.0
        px, py, prumo = self._prevista(t_relogio)
        salto = math.hypot(px - self.disp_xy[0], py - self.disp_xy[1])
        # N8: correção lateral (troca de pista) é suavizada mais devagar que o avanço normal
        k = min(1.0, dt * (5.0 if salto < 8 else 2.5))
        x = self.disp_xy[0] + (px - self.disp_xy[0]) * k
        y = self.disp_xy[1] + (py - self.disp_xy[1]) * k
        if salto > 120:   # salto grande (ex.: reinício do HMM)
            x, y = px, py
        self.disp_xy = (x, y)
        if self.vel_ms > 1.5:
            self.disp_rumo = (self.disp_rumo + _dif(prumo, self.disp_rumo) * min(1.0, dt * 3.0)) % 360
        banda = None
        if self.alerta:
            d = max(0.0, self.alerta.distancia_m - self.vel_ms * dtf)
            banda = (f"RADAR {d:.0f} m" if not self.alerta.via_incerta else f"RADAR ~{d:.0f} m (via incerta)",
                     self.alerta.via_incerta)
        placa = dict(self.placa) if self.placa else None
        if placa:
            placa["dist"] = max(0.0, placa["dist"] - self.vel_ms * dtf)
        # N3: distância que precisa caber na tela à frente (radar do aviso ou da placa)
        alvo_vis = None
        if self.alerta:
            alvo_vis = max(0.0, self.alerta.distancia_m - self.vel_ms * dtf)
        elif placa and placa["dist"] < 700:
            alvo_vis = placa["dist"]
        return {"cx": x, "cy": y, "rumo": self.disp_rumo,
                "vel_kmh": self.vel_ms * 3.6 if (self.gps_ok or self.estimado) else 0.0,
                "radares": self.radares, "ativo_id": self.alerta.radar_id if self.alerta else None,
                "placa": placa, "banda": banda, "via": self.via, "gps_ok": self.gps_ok,
                "estimado": self.estimado, "limite_via": self.limite_via, "dist_alvo": alvo_vis}
