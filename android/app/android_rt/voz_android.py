"""Voz no APK: WAVs do Piper (piper1-gpl, pt_BR-faber-medium) gerados na box e empacotados; o que não
estiver pré-gerado sai pelo TTS do sistema Android (offline com a voz pt-BR instalada no aparelho).
Mesma interface usada pelo Navegador: falar(texto, t) -> registro."""
from __future__ import annotations

import json
import queue
import threading
import time
from pathlib import Path

from radar_pi.voz.tts import Voz as _VozBase


class VozAndroid:
    frase = staticmethod(_VozBase.frase)

    def __init__(self, pasta_wav: Path, tocar=True, tts_sistema=None):
        self.pasta = Path(pasta_wav)
        try:
            self.indice = json.loads((self.pasta / "indice.json").read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            self.indice = {}
        self.tocar = tocar
        self.tts = tts_sistema
        self.log = []
        self.mixer_ok = False
        self._sons = {}
        try:
            import pygame
            pygame.mixer.init(22050, -16, 1, 1024)
            self.mixer_ok = True
        except Exception as e:  # noqa: BLE001
            self.erro_mixer = str(e)
        self._fila = queue.Queue()
        threading.Thread(target=self._worker, daemon=True, name="voz").start()

    @property
    def motor_ativo(self):
        return "piper-wav" if self.mixer_ok and self.indice else ("android-tts" if self.tts else None)

    def falar(self, texto, t=None):
        reg = {"t": t, "texto": texto, "motor": None, "erro": None, "feito": False}
        self.log.append(reg)
        if len(self.log) > 200:
            del self.log[:100]
        self._fila.put(reg)
        return reg

    def _worker(self):
        while True:
            reg = self._fila.get()
            while not self._fila.empty():      # fala só a mais recente
                reg = self._fila.get_nowait()
            try:
                self._executar(reg)
            except Exception as e:  # noqa: BLE001
                reg["erro"] = str(e)[:200]
            reg["feito"] = True

    def _executar(self, reg):
        if not self.tocar:
            reg["motor"] = "mudo"
            return
        nome = self.indice.get(reg["texto"])
        if nome and self.mixer_ok:
            import pygame
            s = self._sons.get(nome)
            if s is None:
                s = self._sons[nome] = pygame.mixer.Sound(str(self.pasta / nome))
            s.play()
            reg["motor"] = "piper-wav"
            time.sleep(s.get_length())
            return
        if self.tts and self.tts.falar(reg["texto"]):
            reg["motor"] = "android-tts"
            return
        reg["erro"] = "sem motor de voz"

    def esperar(self, timeout=5.0):
        fim = time.time() + timeout
        while time.time() < fim and not all(r.get("feito") for r in self.log):
            time.sleep(0.02)
