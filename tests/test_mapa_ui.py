"""Tela de mapa: gpsd (servidor falso), render headless e coerência do HUD."""
import json
import os
import socket
import threading
import time

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import pytest

from radar_pi.mapa_ui.gps import FonteGPSD


def _servidor_gpsd(linhas, porta_box):
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    porta_box.append(srv.getsockname()[1])

    def run():
        c, _ = srv.accept()
        c.recv(1024)
        for l in linhas:
            c.sendall((json.dumps(l) + "\n").encode())
            time.sleep(0.01)
        time.sleep(0.3)
        c.close()
        srv.close()
    threading.Thread(target=run, daemon=True).start()


def test_gpsd_tpv(registrar):
    linhas = [{"class": "VERSION"}, {"class": "TPV", "mode": 3, "lat": -23.52, "lon": -46.70, "speed": 22.2, "track": 95.0, "epx": 6, "epy": 8},
              {"class": "SKY"}, {"class": "TPV", "mode": 1}]
    porta = []
    _servidor_gpsd(linhas, porta)
    g = FonteGPSD("127.0.0.1", porta[0])
    fx = []
    for _ in range(50):
        fx += g.novos()
        if len(fx) >= 2:
            break
        time.sleep(0.02)
    registrar(cenario="gpsd_falso", fixes=[(f.lat, f.lon, f.vel_ms, f.rumo, f.precisao_m) for f in fx])
    assert len(fx) == 2 and fx[0].lat == -23.52 and fx[0].vel_ms == 22.2 and fx[0].rumo == 95.0 and fx[0].precisao_m == 8
    assert not fx[1].valido


def test_app_headless_cenarios(registrar, tmp_path):
    from radar_pi.mapa_ui.app import main
    out = {}
    for c in ("cet_acima", "sem_radar"):
        m = main(["--cenario", c, "--headless", "--duracao", "20", "--png", f"18={tmp_path}/{c}.png"])
        out[c] = m
        assert (tmp_path / f"{c}.png").exists()
    registrar(cenario="mapa_headless", resultado=out)
    assert out["cet_acima"]["voz"] and out["cet_acima"]["voz"][0][1] == "radar, 50"
    assert out["sem_radar"]["voz"] == []
