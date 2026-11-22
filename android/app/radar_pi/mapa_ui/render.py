"""Desenho do mapa minimalista (só ruas, nomes e radares) + HUD, com pygame/SDL."""
from __future__ import annotations

import math
import time

import numpy as np
import pygame

from .base import MapaBase

FONTE = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
FONTE_B = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
FUNDO = (241, 243, 244)
TEXTO_RUA = (70, 74, 80)
VERMELHO = (211, 47, 47)
AZUL = (26, 115, 232)


def _dif(a, b):
    return (a - b + 180) % 360 - 180


class Renderer:
    def __init__(self, base: MapaBase, w: int, h: int, zoom_m_px=1.4, rotacionar=True):
        pygame.font.init()
        self.b, self.w, self.h = base, w, h
        self.rot = rotacionar
        self.s = h / 480.0
        # N3: o zoom base escala com a resolução (mesma distância visível à frente em 480x320 e 800x480)
        self.zoom_base = zoom_m_px / self.s
        self.zoom = self.zoom_base
        self.f_rua = pygame.font.Font(FONTE, max(10, int(13 * self.s)))
        self.f_vel = pygame.font.Font(FONTE_B, int(54 * self.s))
        self.f_peq = pygame.font.Font(FONTE_B, max(10, int(14 * self.s)))
        self.f_placa = pygame.font.Font(FONTE_B, int(40 * self.s))
        self.f_placa2 = pygame.font.Font(FONTE_B, int(24 * self.s))
        self.f_icone = pygame.font.Font(FONTE_B, max(8, int(11 * self.s)))
        self.f_banda = pygame.font.Font(FONTE_B, int(28 * self.s))
        self._txt = {}
        self._rot = {}
        self._banda = None
        self.carro = (w / 2, h * 0.80)
        self.tempos = []
        self.zooms = []

    # --- transformação mundo (UTM, m) -> tela (px), com o rumo para cima ------
    def _matriz(self, cx, cy, rumo):
        a = math.radians(rumo if self.rot else 0.0)
        return cx, cy, math.cos(a), math.sin(a)

    def para_tela(self, xy, M):
        cx, cy, c, s = M
        dx, dy = xy[:, 0] - cx, xy[:, 1] - cy
        xr = dx * c - dy * s
        yr = dx * s + dy * c
        return np.column_stack((self.carro[0] + xr / self.zoom, self.carro[1] - yr / self.zoom))

    # --- texto com cache -------------------------------------------------------
    def _texto_halo(self, txt, fonte, cor):
        k = (txt, id(fonte), cor)
        if k not in self._txt:
            base = fonte.render(txt, True, cor)
            halo = fonte.render(txt, True, (255, 255, 255))
            sf = pygame.Surface((base.get_width() + 4, base.get_height() + 4), pygame.SRCALPHA)
            for dx, dy in ((0, 0), (4, 0), (0, 4), (4, 4), (2, 0), (0, 2), (4, 2), (2, 4)):
                sf.blit(halo, (dx, dy))
            sf.blit(base, (2, 2))
            self._txt[k] = sf
        return self._txt[k]

    def _rotulo(self, nome, ang):
        k = (nome, int(round(ang / 5.0)) * 5)
        if k not in self._rot:
            if len(self._rot) > 3000:
                self._rot.clear()
            self._rot[k] = pygame.transform.rotate(self._texto_halo(nome, self.f_rua, TEXTO_RUA), k[1])
        return self._rot[k]

    # --- camadas ---------------------------------------------------------------
    def _ruas(self, sf, M, cx, cy):
        R = math.hypot(self.w, self.h) * self.zoom * 0.75
        vis = self.b.visiveis(cx - R, cy - R, cx + R, cy + R)
        if not len(vis):
            return [], []
        cs = [self.b.coords(i) for i in vis]
        lens = [len(c) for c in cs]
        tela = self.para_tela(np.concatenate(cs), M)
        partes = np.split(tela, np.cumsum(lens)[:-1])
        for passo in (0, 1):  # 0 = contorno, 1 = preenchimento
            for i, pts in zip(vis, partes):
                w_m, cor, borda, _ = self.b.cls(i)
                wp = max(1, int(w_m / self.zoom))
                larg = wp + 2 if passo == 0 else wp
                c = borda if passo == 0 else cor
                p = pts.tolist()
                pygame.draw.lines(sf, c, False, p, larg)
                if passo == 1 and larg >= 4:
                    r = larg // 2
                    for x, y in p:
                        pygame.draw.circle(sf, c, (x, y), r)
        return vis, partes

    def _nomes(self, sf, vis, partes, icones=()):
        cands = {}
        for i, pts in zip(vis, partes):
            a = self.b.m.attr[i]
            nome = a["nome_orig"]
            if isinstance(nome, list):
                nome = nome[0]
            if not nome or not isinstance(nome, str):
                continue
            d = np.diff(pts, axis=0)
            seg = np.hypot(d[:, 0], d[:, 1])
            cum = np.concatenate(([0.0], np.cumsum(seg)))
            L = cum[-1]
            if L < 40:
                continue
            tw = self.f_rua.size(nome)[0]
            # janela de comprimento tw centrada no meio da polilinha visível
            def pt(sv):
                j = min(int(np.searchsorted(cum, sv, side="right")) - 1, len(seg) - 1)
                f = 0.0 if seg[j] == 0 else (sv - cum[j]) / seg[j]
                return pts[j] + f * d[j]
            a0, a1 = pt(max(0.0, L / 2 - tw / 2)), pt(min(L, L / 2 + tw / 2))
            corda = a1 - a0
            reto = np.hypot(*corda) >= 0.92 * min(tw, L)
            mid = (a0 + a1) / 2
            if not reto or not (20 < mid[0] < self.w - 20 and 20 < mid[1] < self.h - 20):
                continue
            pri = self.b.cls(i)[3]
            k = (pri, -L)
            if nome not in cands or k < cands[nome][0]:
                cands[nome] = (k, mid, corda, L)
        ocupados = [pygame.Rect(0, 0, int(150 * self.s), int(95 * self.s)),
                    pygame.Rect(self.w - int(130 * self.s), 0, int(130 * self.s), int(130 * self.s)),
                    pygame.Rect(self.carro[0] - 30, self.carro[1] - 30, 60, 60),
                    pygame.Rect(0, 0, self.w, int(70 * self.s)) if self._banda else pygame.Rect(0, 0, 0, 0),
                    pygame.Rect(self.w // 2 - int(150 * self.s), self.h - int(45 * self.s), int(300 * self.s), int(45 * self.s))]
        ocupados += list(icones)   # N4: rótulo não fica sob ícone de radar
        tela = pygame.Rect(4, 4, self.w - 8, self.h - 8)
        n = 0
        for nome, (k, mid, d, L) in sorted(cands.items(), key=lambda kv: kv[1][0]):
            tw = self.f_rua.size(nome)[0]
            if L < tw + 12:
                continue
            ang = -math.degrees(math.atan2(d[1], d[0]))
            if ang > 90:
                ang -= 180
            elif ang < -90:
                ang += 180
            img = self._rotulo(nome, ang)
            r = img.get_rect(center=(int(mid[0]), int(mid[1])))
            # N4: o rótulo inteiro dentro da tela (não corta na borda) e sem colidir
            if not tela.contains(r) or r.inflate(-r.w * 0.2, -r.h * 0.2).collidelist(ocupados) != -1:
                continue
            sf.blit(img, r)
            ocupados.append(r)
            n += 1
            if n >= 18:
                break

    def _icones(self, M, radares, ativo_id, rumo_carro):
        """N1/N9: posição na tela, rumo fiscalizado e agrupamento de radares no mesmo ponto."""
        if not radares:
            return []
        xy = np.array([(r["_x"], r["_y"]) for r in radares])
        tela = self.para_tela(xy, M)
        rr = int(11 * self.s)
        grupos = []
        for r, (x, y) in zip(radares, tela):
            if not (-20 < x < self.w + 20 and -20 < y < self.h + 20):
                continue
            for g in grupos:   # N9: junta ícones a menos de 1,2 raio na tela
                if math.hypot(g["x"] - x, g["y"] - y) < rr * 1.2 and \
                        (g["r"].get("_rumo") is None or r.get("_rumo") is None or abs(_dif(g["r"]["_rumo"], r["_rumo"])) < 60):
                    g["n"] += 1
                    if r["id"] == ativo_id:
                        g["r"], g["ativo"] = r, True
                    break
            else:
                grupos.append({"r": r, "x": x, "y": y, "n": 1, "ativo": r["id"] == ativo_id})
        for g in grupos:
            ru = g["r"].get("_rumo")
            # N1: radar do sentido contrário ao carro fica apagado
            g["oposto"] = ru is not None and abs(_dif(ru, rumo_carro)) > 100 and not g["ativo"]
            g["rect"] = pygame.Rect(int(g["x"]) - rr - 2, int(g["y"]) - rr - 2, 2 * rr + 4, 2 * rr + 4)
        return grupos

    def _radares(self, sf, grupos, rumo_vista):
        rr = int(11 * self.s)
        cinza = (190, 193, 198)
        # opostos primeiro (ficam por baixo)
        for g in sorted(grupos, key=lambda g: (not g["oposto"], g["ativo"])):
            r, x, y = g["r"], int(g["x"]), int(g["y"])
            lim = r.get("_lim") or r.get("limite_kmh")
            ru = r.get("_rumo")
            if g["oposto"]:
                rp = int(rr * 0.8)
                pygame.draw.circle(sf, (248, 248, 249), (x, y), rp)
                pygame.draw.circle(sf, cinza, (x, y), rp, max(1, int(2 * self.s)))
                if lim:
                    t = self.f_icone.render(str(lim), True, (160, 163, 168))
                    sf.blit(t, t.get_rect(center=(x, y)))
                continue
            if g["ativo"]:
                pygame.draw.circle(sf, (255, 205, 210), (x, y), rr * 2)
                pygame.draw.circle(sf, VERMELHO, (x, y), rr * 2, 2)
            cor_anel = VERMELHO if ru is not None or r.get("_conf", 1) >= 0.5 else (230, 81, 0)
            if ru is not None:   # N1: seta do sentido fiscalizado na borda do ícone
                a = math.radians(ru - (rumo_vista if self.rot else 0.0))
                ponta = (x + math.sin(a) * rr * 1.75, y - math.cos(a) * rr * 1.75)
                b1 = (x + math.sin(a + 0.55) * rr * 0.95, y - math.cos(a + 0.55) * rr * 0.95)
                b2 = (x + math.sin(a - 0.55) * rr * 0.95, y - math.cos(a - 0.55) * rr * 0.95)
                pygame.draw.polygon(sf, cor_anel, [ponta, b1, b2])
            if lim:
                pygame.draw.circle(sf, (255, 255, 255), (x, y), rr)
                pygame.draw.circle(sf, cor_anel, (x, y), rr, max(2, int(3 * self.s)))
                t = self.f_icone.render(str(lim), True, (20, 20, 20))
                sf.blit(t, t.get_rect(center=(x, y)))
            else:  # radar sem limite oficial: ícone de câmera
                q = pygame.Rect(0, 0, int(rr * 1.9), int(rr * 1.4))
                q.center = (x, y)
                pygame.draw.rect(sf, (55, 71, 79), q, border_radius=4)
                pygame.draw.circle(sf, (255, 255, 255), (x, y), int(rr * 0.45))
                pygame.draw.circle(sf, (55, 71, 79), (x, y), int(rr * 0.25))
            if ru is None and r.get("_conf", 1) < 0.5:   # sentido não confirmado
                t = self.f_icone.render("?", True, (230, 81, 0))
                sf.blit(t, t.get_rect(center=(x + rr, y - rr)))
            if g["n"] > 1:   # N9: contador de radares agrupados
                t = self.f_icone.render(f"x{g['n']}", True, (255, 255, 255))
                bx = t.get_rect(center=(x + rr, y + rr))
                pygame.draw.rect(sf, (60, 64, 67), bx.inflate(6, 2), border_radius=6)
                sf.blit(t, bx)

    def _seta(self, sf, rumo_tela, gps_ok):
        x, y = self.carro
        L = 20 * self.s
        a = math.radians(rumo_tela)
        def P(ang, r):
            return (x + r * math.sin(a + ang), y - r * math.cos(a + ang))
        pts = [P(0, L), P(math.radians(140), L * 0.85), P(math.pi, L * 0.35), P(math.radians(-140), L * 0.85)]
        halo = pygame.Surface((int(L * 4), int(L * 4)), pygame.SRCALPHA)
        pygame.draw.circle(halo, (26, 115, 232, 45) if gps_ok else (120, 120, 120, 45), (int(L * 2), int(L * 2)), int(L * 1.8))
        sf.blit(halo, (x - L * 2, y - L * 2))
        pygame.draw.polygon(sf, AZUL if gps_ok else (130, 130, 130), pts, 0)
        pygame.draw.polygon(sf, (255, 255, 255), pts, max(2, int(3 * self.s)))

    def _hud(self, sf, vel, placa, banda, via, gps_ok, limite_via=None, estimado=False):
        s = self.s
        # velocidade (sup. esquerdo). N7: sem radar, compara com o maxspeed OSM da via (estimado)
        ref = placa["limite"] if placa and placa.get("limite") else limite_via
        acima = bool(ref and vel > ref)
        box = pygame.Rect(int(10 * s), int(10 * s), int(130 * s), int(82 * s))
        pygame.draw.rect(sf, (200, 202, 206), box.move(2, 3), border_radius=12)
        pygame.draw.rect(sf, (255, 255, 255), box, border_radius=12)
        if acima:
            pygame.draw.rect(sf, VERMELHO, box, max(2, int(3 * s)), border_radius=12)
        t = self.f_vel.render(f"{vel:.0f}", True, VERMELHO if acima else (32, 33, 36))
        sf.blit(t, t.get_rect(center=(box.centerx, box.y + int(34 * s))))
        t = self.f_peq.render("km/h", True, (95, 99, 104))
        sf.blit(t, t.get_rect(center=(box.centerx, box.bottom - int(13 * s))))
        # placa do próximo radar na via (sup. direito)
        r = int(48 * s)
        c = (self.w - int(14 * s) - r, int(12 * s) + r)
        if placa:
            pygame.draw.circle(sf, (200, 202, 206), (c[0] + 2, c[1] + 3), r)
            pygame.draw.circle(sf, (255, 255, 255), c, r)
            pygame.draw.circle(sf, VERMELHO, c, r, max(5, int(9 * s)))
            txt = placa["texto"]
            f = self.f_placa if len(txt) <= 3 else self.f_placa2
            t = f.render(txt, True, (20, 20, 20))
            sf.blit(t, t.get_rect(center=c))
            sub = f"{placa['dist']:.0f} m" + ("  (OSM)" if placa.get("estimado") else "")
            t = self._texto_halo(sub, self.f_peq, (60, 64, 67))
            sf.blit(t, t.get_rect(center=(c[0], c[1] + r + int(13 * s))))
        elif limite_via:
            # N7: limite da via pelo OSM, placa "apagada" com asterisco (estimado, não é radar)
            pygame.draw.circle(sf, (255, 255, 255), c, r)
            pygame.draw.circle(sf, (229, 115, 115), c, r, max(4, int(7 * s)))
            txt = f"{limite_via}*"
            t = (self.f_placa if len(txt) <= 3 else self.f_placa2).render(txt, True, (95, 99, 104))
            sf.blit(t, t.get_rect(center=c))
            t = self._texto_halo("via (OSM)", self.f_peq, (95, 99, 104))
            sf.blit(t, t.get_rect(center=(c[0], c[1] + r + int(13 * s))))
        else:
            pygame.draw.circle(sf, (255, 255, 255), c, r)
            pygame.draw.circle(sf, (189, 193, 198), c, r, max(3, int(5 * s)))
            t = self.f_placa2.render("--", True, (154, 160, 166))
            sf.blit(t, t.get_rect(center=c))
        # faixa de alerta (topo, centro)
        if banda:
            txt, incerta = banda
            t = self.f_banda.render(txt, True, (255, 255, 255))
            bx = pygame.Rect(0, 0, t.get_width() + int(36 * s), int(50 * s))
            bx.midtop = (self.w // 2, int(12 * s))
            pygame.draw.rect(sf, (230, 81, 0) if incerta else VERMELHO, bx, border_radius=int(25 * s))
            sf.blit(t, t.get_rect(center=bx.center))
        # nome da via (rodapé)
        if via:
            t = self.f_peq.render(via, True, (255, 255, 255))
            bx = pygame.Rect(0, 0, t.get_width() + int(28 * s), int(30 * s))
            bx.midbottom = (self.w // 2, self.h - int(10 * s))
            pygame.draw.rect(sf, (60, 64, 67), bx, border_radius=int(15 * s))
            sf.blit(t, t.get_rect(center=bx.center))
        if not gps_ok:
            msg = "SEM GPS - posição estimada" if estimado else "SEM SINAL GPS"
            t = self._texto_halo(msg, self.f_peq, VERMELHO)
            sf.blit(t, (int(12 * s), int(100 * s)))

    def _auto_zoom(self, dist_alvo, dt=0.05):
        """N3: afasta o zoom para o radar do aviso caber entre o carro e a faixa de alerta."""
        alvo = self.zoom_base
        if dist_alvo is not None:
            px_livres = self.carro[1] - 75 * self.s
            alvo = min(max(self.zoom_base, (dist_alvo + 40) / px_livres), self.zoom_base * 3.5)
        self.zoom += (alvo - self.zoom) * min(1.0, 4.0 * dt) if alvo < self.zoom else (alvo - self.zoom) * min(1.0, 8.0 * dt)
        return self.zoom

    def desenhar(self, sf, cx, cy, rumo, vel_kmh, radares, ativo_id=None, placa=None, banda=None, via="", gps_ok=True,
                 limite_via=None, estimado=False, dist_alvo=None, dt=0.05):
        t0 = time.perf_counter()
        self._banda = banda
        self._auto_zoom(dist_alvo, dt)
        self.zooms.append(self.zoom)
        sf.fill(FUNDO)
        M = self._matriz(cx, cy, rumo)
        vis, partes = self._ruas(sf, M, cx, cy)
        grupos = self._icones(M, radares, ativo_id, rumo)
        self._nomes(sf, vis, partes, [g["rect"] for g in grupos])
        self._radares(sf, grupos, rumo)
        self._seta(sf, 0.0 if self.rot else rumo, gps_ok or estimado)
        self._hud(sf, vel_kmh, placa, banda, via, gps_ok, limite_via, estimado)
        ms = (time.perf_counter() - t0) * 1000
        self.tempos.append(ms)
        return ms

    def radar_na_tela(self, cx, cy, rumo, xy):
        """Para testes do N3: o ponto xy (mundo) cai dentro da área visível abaixo da faixa?"""
        M = self._matriz(cx, cy, rumo)
        p = self.para_tela(np.array([xy]), M)[0]
        return 0 <= p[0] <= self.w and 70 * self.s <= p[1] <= self.h
