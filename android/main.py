"""radar-pi para Android (APK de teste). Mesma tela e mesma lógica do Pi: mapa mínimo, HMM, alerta a 450 m
na via/sentido, voz "radar, <limite>". Sem botões.

Modo simulação (sem botão na tela), qualquer um destes:
  1) arquivo  Android/data/org.radarpi.radarpi/files/radar.env  com a linha  RADAR_GPS=sim
  2) adb shell am start -n org.radarpi.radarpi/org.kivy.android.PythonActivity --es RADAR_GPS sim
  3) gesto escondido: segurar o dedo na tela por 3 s alterna GPS real <-> simulação
"""
from __future__ import annotations

import json
import os
import sys
import time
import traceback
from pathlib import Path

AQUI = Path(__file__).resolve().parent
NO_ANDROID = "ANDROID_ARGUMENT" in os.environ or "ANDROID_PRIVATE" in os.environ
if NO_ANDROID or os.environ.get("RADAR_COMPAT") == "1":
    sys.path.insert(0, str(AQUI / "compat"))   # pyproj/shapely/h3 puros (ver compat/)
sys.path.insert(0, str(AQUI))

from android_rt import diag  # noqa: E402  (só stdlib + jnius; nunca levanta exceção)

log = diag.log
plat = None
EXT = None


def _seguro(nome, f, *a):
    """Passo opcional: se falhar, registra e segue (não derruba o app)."""
    try:
        return f(*a)
    except Exception:  # noqa: BLE001
        log(f"falhou (seguindo sem): {nome}\n" + traceback.format_exc())
        return None


def carregar_config():
    os.environ.setdefault("RADAR_TELEMETRIA", "0")
    os.environ.setdefault("RADAR_VOZ_DIR", str(Path(EXT or AQUI) / "voz"))
    os.environ.setdefault("RADAR_GPS", "gps")
    cfg = {}
    if EXT:
        f = Path(EXT) / "radar.env"
        if f.exists():
            for linha in f.read_text(encoding="utf-8", errors="ignore").splitlines():
                linha = linha.strip()
                if linha and not linha.startswith("#") and "=" in linha:
                    k, v = linha.split("=", 1)
                    cfg[k.strip()] = v.strip()
    cfg.update(plat.extras_intent())
    for k, v in cfg.items():
        if k.startswith("RADAR_"):
            os.environ[k] = v
    return cfg


class SimLoop:
    """Reproduz, em loop, os trajetos de simulação gerados na box com o código real (data/sim/*.json)."""

    def __init__(self, pasta: Path):
        from radar_pi.mapmatch.hmm import Fix
        ordem = json.loads((pasta / "ordem.json").read_text())
        pedido = os.environ.get("RADAR_CENARIO")
        if pedido:
            ordem = [c for c in pedido.split(",") if (pasta / f"{c}.json").exists()] or ordem
        self.cen = []
        for nome in ordem:
            d = json.loads((pasta / f"{nome}.json").read_text())
            self.cen.append((nome, [Fix(*x) for x in d["fixes"]]))
        self.k = -1
        self.proximo(0.0)

    def proximo(self, t):
        self.k = (self.k + 1) % len(self.cen)
        self.nome, self.fixes = self.cen[self.k]
        self.t_ini, self.j = t, 0
        log("simulação:", self.nome, len(self.fixes), "fixes")

    def novos(self, t):
        out = []
        f0 = self.fixes[0].t
        while self.j < len(self.fixes) and self.fixes[self.j].t - f0 <= t - self.t_ini:
            f = self.fixes[self.j]
            out.append(type(f)(t, f.lat, f.lon, f.vel_ms, f.rumo, f.precisao_m))
            self.j += 1
        return out

    def acabou(self, t):
        return self.j >= len(self.fixes) and t - self.t_ini > self.fixes[-1].t - self.fixes[0].t + 4.0


def main():
    global plat, EXT
    log("passo: android_rt.plataforma")
    from android_rt import plataforma as plat
    EXT = plat.pasta_externa()
    carregar_config()
    log("passo: config")
    from radar_pi import config
    log("passo: import pygame")
    import pygame
    log("pygame", pygame.version.ver, "| passo: import numpy")
    import numpy
    log("numpy", numpy.__version__, "| passo: import sqlite3")
    import sqlite3
    log("sqlite", sqlite3.sqlite_version, "| passo: módulos radar_pi")
    from radar_pi.dados import db
    from radar_pi.mapa.tiles import MalhaTiles
    from radar_pi.mapa_ui import render as rmod
    from radar_pi.mapa_ui.base import MapaBase
    from radar_pi.mapa_ui.navegador import Navegador
    from android_rt.voz_android import VozAndroid

    rmod.FONTE = str(AQUI / "fonts/DejaVuSans.ttf")
    rmod.FONTE_B = str(AQUI / "fonts/DejaVuSans-Bold.ttf")
    log("início; config:", {k: v for k, v in os.environ.items() if k.startswith("RADAR_")})

    _seguro("manter tela ligada", plat.manter_tela_ligada)
    _seguro("pedir permissão", plat.pedir_permissao_localizacao)
    log("passo: pygame.init")
    pygame.init()
    # superfície lógica com 480 px de altura (igual ao Pi 800x480), escalada pela GPU para a tela inteira
    H = 480
    try:
        fw, fh = plat.tamanho_tela() if NO_ANDROID else (800, 480)
    except Exception:  # noqa: BLE001
        info = pygame.display.Info()
        fw, fh = info.current_w, info.current_h
    if fh > fw:
        fw, fh = fh, fw   # APK travado em paisagem
    W = max(640, int(round(H * fw / fh)))
    try:
        tela = pygame.display.set_mode((W, H), pygame.FULLSCREEN | pygame.SCALED if NO_ANDROID else 0)
    except Exception as e:  # noqa: BLE001
        log("SCALED falhou, usando resolução nativa:", e)
        tela = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
        W, H = tela.get_size()
    log("tela lógica", W, H, "física", fw, fh)

    log("passo: malha", config.GRAPH_TILES, os.path.exists(config.GRAPH_TILES))
    malha = MalhaTiles(config.GRAPH_TILES, "sp")
    base = MapaBase(malha)
    log("passo: banco", config.DB_PATH, os.path.exists(config.DB_PATH))
    con = db.conectar()
    log("passo: voz")
    tts = _seguro("TTS do Android", plat.TTSAndroid)
    voz = VozAndroid(AQUI / "data/voz", tocar=True, tts_sistema=tts)
    log("voz:", voz.motor_ativo, "| passo: render")
    rend = rmod.Renderer(base, W, H, config.ZOOM_M_PX, config.ROTACIONAR)
    f_msg = pygame.font.Font(rmod.FONTE, 22)
    log("passo: gps")
    gps = plat.GPSAndroid()
    import threading
    from radar_pi.mapa.tiles import RES as RES_TILES
    import h3 as _h3
    _tl = threading.local()

    def cobertura(lat, lon):
        """Há malha carregável nesta posição? (célula da grade com arestas). Conexão própria por thread."""
        c = getattr(_tl, "con", None)
        if c is None:
            c = _tl.con = sqlite3.connect(str(config.GRAPH_TILES))
        cel = _h3.latlng_to_cell(lat, lon, RES_TILES)
        return c.execute("SELECT 1 FROM aresta_cel WHERE cel=? LIMIT 1", (cel,)).fetchone() is not None
    gps.cobertura = cobertura
    fora_area = False
    f_peq = pygame.font.Font(rmod.FONTE, 15)

    modo = "sim" if os.environ.get("RADAR_GPS", "gps").lower() == "sim" else "gps"
    sim = None
    nav = Navegador(malha, con, voz)

    def trocar_modo(novo, t):
        nonlocal modo, sim, nav
        modo = novo
        nav = Navegador(malha, con, voz)
        if modo == "sim":
            _seguro("parar GPS", gps.parar)
            sim = SimLoop(AQUI / "data/sim")
            sim.t_ini = t
        else:
            sim = None
        log("modo:", modo)

    trocar_modo(modo, 0.0)
    fps = min(config.FPS, 20)
    dt = 1.0 / fps
    rel = pygame.time.Clock()
    t0 = time.monotonic()
    toque_ini = None
    toque_pos = None
    perm_log = None
    rodando = True
    teste_seg = float(os.environ.get("RADAR_TESTE_SEG", 0))      # só para teste na box (headless)
    teste_png = os.environ.get("RADAR_TESTE_PNG")                # pasta: salva 1 PNG por segundo
    n_png = 0
    primeiro_quadro = False
    while rodando:
        t = time.monotonic() - t0
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT or (ev.type == pygame.KEYDOWN and ev.key in (pygame.K_AC_BACK, pygame.K_ESCAPE)):
                rodando = False
            elif ev.type == pygame.MOUSEBUTTONDOWN:
                toque_ini, toque_pos = t, ev.pos
            elif ev.type == pygame.MOUSEMOTION and toque_pos is not None:
                if abs(ev.pos[0] - toque_pos[0]) + abs(ev.pos[1] - toque_pos[1]) > 40:
                    toque_ini = toque_pos = None              # arrastou: não é o gesto
            elif ev.type in (pygame.MOUSEBUTTONUP, pygame.FINGERUP, pygame.WINDOWLEAVE, pygame.WINDOWFOCUSLOST):
                toque_ini = toque_pos = None
        if toque_ini is not None and t - toque_ini >= 4.0:     # gesto escondido: segurar 4 s parado
            toque_ini = None
            trocar_modo("gps" if modo == "sim" else "sim", t)
        if modo == "gps" and not gps.ativo:
            perm = _seguro("permissão?", plat.tem_permissao_localizacao)
            if perm != perm_log:
                perm_log = perm
                log("permissão de localização concedida:", perm)
            if perm:
                _seguro("iniciar GPS", gps.iniciar)   # só pede de verdade a cada 5 s até subir
        fonte = sim if modo == "sim" else gps
        for f in fonte.novos(t):
            nav.novo_fix(f, t)
            if modo == "gps" and f.valido:
                fora_area = not _seguro("cobertura", cobertura, f.lat, f.lon)
        if modo == "sim" and sim.acabou(t):
            sim.proximo(t)
            nav = Navegador(malha, con, voz)
        e = nav.quadro(t, dt)
        if e:
            rend.desenhar(tela, e["cx"], e["cy"], e["rumo"], e["vel_kmh"], e["radares"], e["ativo_id"], e["placa"],
                          e["banda"], e["via"], e["gps_ok"], e["limite_via"], e["estimado"], e["dist_alvo"], dt)
            if modo == "gps" and fora_area:
                s_ = f_peq.render("fora da área do mapa", True, rmod.TEXTO_RUA)
                tela.blit(s_, ((W - s_.get_width()) // 2, H - s_.get_height() - 4))
        else:
            tela.fill(rmod.FUNDO)
            if modo == "gps":
                txt = ("aguardando GPS…" if not NO_ANDROID or _seguro("permissão?", plat.tem_permissao_localizacao)
                       else "permita a localização para o radar-pi")
                s = f_msg.render(txt, True, rmod.TEXTO_RUA)
                tela.blit(s, ((W - s.get_width()) // 2, (H - s.get_height()) // 2))
        pygame.display.flip()
        if not primeiro_quadro:
            primeiro_quadro = True
            diag.esconder_loading()
            log("primeiro quadro desenhado; modo", modo)
        if teste_png and t >= n_png:
            pygame.image.save(tela, f"{teste_png}/t{n_png:03d}.png")
            n_png += 1
        if teste_seg and t >= teste_seg:
            log("voz:", [(round(r["t"] or 0, 1), r["texto"], r["motor"], r["erro"]) for r in voz.log])
            rodando = False
        rel.tick(fps)
    gps.parar()
    pygame.quit()


if __name__ == "__main__":
    diag.iniciar()
    try:
        main()
        log("fim normal")
    except BaseException:  # noqa: BLE001 - nada de fechar calado: mostra o erro na tela
        diag.mostrar_erro(traceback.format_exc(), str(AQUI / "fonts/DejaVuSans.ttf"))
