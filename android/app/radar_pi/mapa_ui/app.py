"""Tela principal: mapa em tempo real (pygame/SDL). Pi: fullscreen + gpsd. Box: simulação/headless.

Exemplos:
  python -m radar_pi.mapa_ui.app --gps gpsd                      # no Pi (tela cheia via .env)
  python -m radar_pi.mapa_ui.app --cenario marginal              # simulação com janela
  python -m radar_pi.mapa_ui.app --cenario marginal --headless --video resultados/sim.mp4
"""
from __future__ import annotations

import argparse
import json
import os
import statistics as st
import subprocess
import sys
import time
from pathlib import Path

from radar_pi import config


def carregar_malha(nome=None):
    """N10: malha por tiles H3 (SQLite), carregada sob demanda em volta do carro."""
    from radar_pi.mapa.tiles import MalhaTiles
    return MalhaTiles(config.GRAPH_TILES, "sp")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--gps", default=config.GPS, choices=["sim", "gpsd"])
    ap.add_argument("--cenario", default="marginal", help="marginal | cet_acima | tamoios | sem_radar")
    ap.add_argument("--tela", default=f"{config.TELA_W}x{config.TELA_H}")
    ap.add_argument("--fps", type=int, default=config.FPS)
    ap.add_argument("--headless", action="store_true")
    ap.add_argument("--video", default=None)
    ap.add_argument("--duracao", type=float, default=None)
    ap.add_argument("--png", default="", help="lista t=arquivo.png separada por vírgula")
    ap.add_argument("--metricas", default=None)
    ap.add_argument("--seed", type=int, default=1)
    a = ap.parse_args(argv)

    if a.headless:
        os.environ["SDL_VIDEODRIVER"] = "dummy"
        os.environ["SDL_AUDIODRIVER"] = "dummy"
    import pygame
    from radar_pi.dados import db
    from radar_pi.mapa_ui.base import MapaBase
    from radar_pi.mapa_ui.cenarios import CENARIOS, trajeto
    from radar_pi.mapa_ui.gps import FonteGPSD, FonteSimulada
    from radar_pi.mapa_ui.navegador import Navegador
    from radar_pi.mapa_ui.render import Renderer
    from radar_pi.voz.tts import Voz

    W, H = (int(v) for v in a.tela.lower().split("x"))
    t0 = time.perf_counter()
    malha = carregar_malha()
    t_malha = time.perf_counter() - t0
    base = MapaBase(malha)
    t_base = time.perf_counter() - t0 - t_malha
    con = db.conectar()
    # N2: fala fora do thread do render; Piper com frases comuns pré-geradas (cache) e espeak-ng de fallback
    voz = Voz(tocar=not a.headless and a.gps == "gpsd", assincrono=True, pre_cache=True)
    nav = Navegador(malha, con, voz)
    # telemetria local anonimizada (só agregados por ponto; só com GPS real). Não aparece na tela.
    tel = None
    if config.TELEMETRIA and a.gps == "gpsd":
        from radar_pi.telemetria.agregador import Telemetria
        tel = Telemetria(config.TELEMETRIA_DB, k_min=config.TELEMETRIA_K_MIN)
    info = {}
    if a.gps == "gpsd":
        fonte = FonteGPSD(config.GPSD_HOST, config.GPSD_PORT)
    else:
        fixes, info = trajeto(a.cenario, malha, con, seed=a.seed)
        fonte = FonteSimulada(fixes)

    import gc
    gc.collect()
    gc.freeze()   # E9: tira malha/banco/fontes da coleta de lixo (evita pausas longas no meio do quadro)
    pygame.init()
    if a.headless or a.video:
        tela = pygame.Surface((W, H))
        janela = pygame.display.set_mode((1, 1)) if not a.headless else None
    else:
        flags = pygame.FULLSCREEN if config.FULLSCREEN else 0
        janela = pygame.display.set_mode((W, H), flags)
        pygame.display.set_caption("radar-pi")
        pygame.mouse.set_visible(not config.FULLSCREEN)
        tela = janela
    rend = Renderer(base, W, H, config.ZOOM_M_PX, config.ROTACIONAR)
    pngs = {}
    for item in filter(None, a.png.split(",")):
        t, p = item.split("=")
        pngs[float(t)] = p

    ff = None
    if a.video:
        Path(a.video).parent.mkdir(parents=True, exist_ok=True)
        tmp = a.video + ".semaudio.mp4"
        ff = subprocess.Popen(["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
                               "-s", f"{W}x{H}", "-r", str(a.fps), "-i", "-", "-c:v", "libx264",
                               "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p", tmp], stdin=subprocess.PIPE)

    simulado = a.headless or a.video
    dt = 1.0 / a.fps
    t_ini = time.monotonic()
    k = 0
    quadros_ms, render_ms, ok_vis = [], [], []
    rel = pygame.time.Clock()
    rodando = True
    while rodando:
        tq = time.perf_counter()
        t = k * dt if simulado else time.monotonic() - t_ini
        if not simulado:
            for ev in pygame.event.get():
                if ev.type == pygame.QUIT or (ev.type == pygame.KEYDOWN and ev.key in (pygame.K_ESCAPE, pygame.K_q)):
                    rodando = False
        for f in fonte.novos(t):
            nav.novo_fix(f, t)
            if tel and f.valido:
                tel.alimentar(f.lat, f.lon, f.vel_ms * 3.6, f.rumo)
        e = nav.quadro(t, dt)
        if e:
            render_ms.append(rend.desenhar(tela, e["cx"], e["cy"], e["rumo"], e["vel_kmh"], e["radares"], e["ativo_id"],
                                           e["placa"], e["banda"], e["via"], e["gps_ok"], e["limite_via"], e["estimado"],
                                           e["dist_alvo"], dt))
            if e["dist_alvo"] is not None and nav.alerta:
                ok_vis.append(_radar_visivel(rend, e, nav))
        for tp in [x for x in pngs if t >= x]:
            pygame.image.save(tela, pngs.pop(tp))
        if ff:
            ff.stdin.write(pygame.image.tobytes(tela, "RGB"))
        if not simulado:
            pygame.display.flip()
        quadros_ms.append((time.perf_counter() - tq) * 1000)
        k += 1
        if not simulado:
            rel.tick(a.fps)
        if (a.duracao and t >= a.duracao) or (fonte.fim and a.gps == "sim" and t > fonte.fixes[-1].t - fonte.fixes[0].t + 1):
            rodando = False
    voz.esperar(15)
    if tel:
        tel.fechar()      # descarta os últimos 500 m (destino) e o buffer em memória
    if ff:
        ff.stdin.close()
        ff.wait()
        _mux_voz(a.video + ".semaudio.mp4", a.video, nav.eventos_voz)
    pygame.quit()
    r = sorted(render_ms)
    met = {"cenario": a.cenario, "tela": f"{W}x{H}", "quadros": len(r), "radar": info.get("radar"),
           "render_ms_p50": round(st.median(r), 2), "render_ms_p95": round(r[int(.95 * len(r)) - 1], 2),
           "render_ms_max": round(r[-1], 2), "quadro_ms_p95": round(sorted(quadros_ms)[int(.95 * len(quadros_ms)) - 1], 2),
           "fps_max_render_p95": round(1000 / r[int(.95 * len(r)) - 1], 1), "carga_malha_s": round(t_malha, 1),
           "mapabase_s": round(t_base, 1), "voz": [(round(tv, 1), reg["texto"]) for tv, reg in nav.eventos_voz],
           "voz_motor": [(reg.get("motor"), reg.get("cache"), reg.get("ms")) for _, reg in nav.eventos_voz],
           "voz_cache": voz.cache_info, "tts": voz.motor_ativo,
           "voz_ms_chamada_max": max((reg.get("ms_chamada", 0) for _, reg in nav.eventos_voz), default=None),
           "pct_quadros_alerta_radar_visivel": round(100 * sum(ok_vis) / len(ok_vis), 1) if ok_vis else None,
           "zoom_m_px_min_max": [round(min(rend.zooms), 2), round(max(rend.zooms), 2)] if rend.zooms else None,
           "correcao_por_fix_m": _stats(nav.correcoes), "cargas_tiles": getattr(malha, "cargas", None),
           "rss_MB": _rss()}
    print(json.dumps(met, ensure_ascii=False))
    if a.metricas:
        with open(a.metricas, "a") as f:
            f.write(json.dumps(met, ensure_ascii=False) + "\n")
    return met


def _stats(v):
    if not v:
        return None
    v = sorted(v)
    return {"n": len(v), "mediana": round(st.median(v), 1), "p95": round(v[int(.95 * len(v)) - 1], 1), "max": round(v[-1], 1)}


def _rss():
    try:
        import psutil
        return round(psutil.Process().memory_info().rss / 1e6)
    except Exception:
        return None


def _radar_visivel(rend, e, nav):
    """N3: o radar do aviso aparece na tela (abaixo da faixa)?"""
    r = next((x for x in nav.radares if x["id"] == nav.alerta.radar_id), None)
    if r is None:
        return False
    return rend.radar_na_tela(e["cx"], e["cy"], e["rumo"], (r["_x"], r["_y"]))


def _mux_voz(video_mudo, saida, eventos):
    wavs = [(t, reg["wav"]) for t, reg in eventos if reg.get("wav")]
    if not wavs:
        os.replace(video_mudo, saida)
        return
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", video_mudo]
    filt = []
    for n, (t, w) in enumerate(wavs):
        cmd += ["-i", w]
        filt.append(f"[{n + 1}]adelay={int(t * 1000)}|{int(t * 1000)}[a{n}]")
    filt.append("".join(f"[a{n}]" for n in range(len(wavs))) + f"amix=inputs={len(wavs)}:normalize=0,apad[a]")
    cmd += ["-filter_complex", ";".join(filt), "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-shortest", saida]
    subprocess.run(cmd, check=True)
    os.remove(video_mudo)


if __name__ == "__main__":
    main()
