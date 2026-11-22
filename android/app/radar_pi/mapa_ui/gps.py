"""Fontes de GPS: rota sintética (simulação) ou gpsd real (Pi). Ambas entregam `Fix` a ~1 Hz."""
from __future__ import annotations

import json
import socket
import time
from typing import Iterator, Optional

from radar_pi.mapmatch.hmm import Fix


class FonteSimulada:
    """Entrega os fixes de uma lista no relógio da simulação (t do fix)."""

    def __init__(self, fixes: list[Fix]):
        self.fixes = fixes
        self.k = 0

    def novos(self, t_sim: float) -> list[Fix]:
        out = []
        while self.k < len(self.fixes) and self.fixes[self.k].t - self.fixes[0].t <= t_sim:
            out.append(self.fixes[self.k])
            self.k += 1
        return out

    @property
    def fim(self):
        return self.k >= len(self.fixes)


class FonteGPSD:
    """Cliente mínimo do gpsd (JSON, porta 2947), sem dependências. Não bloqueia o render."""

    def __init__(self, host="127.0.0.1", port=2947, timeout=0.0):
        self.sock = socket.create_connection((host, port), timeout=3)
        self.sock.sendall(b'?WATCH={"enable":true,"json":true};\n')
        self.sock.setblocking(False)
        self.buf = b""
        self.t0 = time.monotonic()

    @staticmethod
    def tpv_para_fix(msg: dict, t: float) -> Optional[Fix]:
        if msg.get("class") != "TPV":
            return None
        if msg.get("mode", 0) < 2 or "lat" not in msg:
            return Fix(t, None, None, 0.0, None, 99.0)   # sem fix (túnel/garagem)
        prec = max(msg.get("epx", 10.0) or 10.0, msg.get("epy", 10.0) or 10.0)
        return Fix(t, msg["lat"], msg["lon"], float(msg.get("speed", 0.0) or 0.0), msg.get("track"), prec)

    def novos(self, t_sim: float = 0.0) -> list[Fix]:
        try:
            while True:
                chunk = self.sock.recv(65536)
                if not chunk:
                    break
                self.buf += chunk
        except (BlockingIOError, InterruptedError):
            pass
        out = []
        while b"\n" in self.buf:
            linha, self.buf = self.buf.split(b"\n", 1)
            try:
                f = self.tpv_para_fix(json.loads(linha), time.monotonic() - self.t0)
            except ValueError:
                continue
            if f:
                out.append(f)
        return out

    fim = False
