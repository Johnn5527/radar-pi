"""Voz offline dos avisos.

Motores: Piper TTS (OHF-Voice/piper1-gpl, `pip install piper-tts`, API Python `PiperVoice`) e espeak-ng
como fallback. RADAR_TTS=piper|espeak escolhe o principal; se o Piper falhar (pacote/voz ausente ou erro
na síntese) a fala sai pelo espeak-ng.

- Cache: as frases comuns ("radar, 40" … "radar, 120", "radar adiante") são sintetizadas uma vez e
  guardadas em WAV (RADAR_VOZ_CACHE); no Pi o aviso toca o WAV pronto (latência de síntese zero).
- N2: com assincrono=True a síntese/reprodução roda numa thread com fila; o render nunca espera.
  Fala nova substitui a que ainda está na fila.
- Simulação (tocar=False): só gera o WAV e registra no log (o vídeo mixa depois)."""
from __future__ import annotations

import hashlib
import queue
import shutil
import subprocess
import threading
import time
import wave
from pathlib import Path

from radar_pi import config

LIMITES_COMUNS = list(range(20, 130, 10))


def frases_comuns():
    return [Voz.frase(v) for v in LIMITES_COMUNS] + [Voz.frase(None)]


class MotorPiper:
    """Wrapper da API Python do Piper (carrega o modelo .onnx uma vez)."""

    def __init__(self, modelo, length_scale=1.0):
        from piper import PiperVoice, SynthesisConfig   # import tardio: pacote opcional
        self.modelo = Path(modelo)
        if not self.modelo.exists():
            raise FileNotFoundError(f"voz Piper não encontrada: {self.modelo}")
        t0 = time.perf_counter()
        self.voz = PiperVoice.load(str(self.modelo))
        self.ms_carga = round((time.perf_counter() - t0) * 1000, 1)
        self.cfg = SynthesisConfig(length_scale=length_scale)
        self.length_scale = length_scale

    def sintetizar(self, texto, wav_path):
        with wave.open(str(wav_path), "wb") as w:
            self.voz.synthesize_wav(texto, w, syn_config=self.cfg)


class Voz:
    def __init__(self, motor=None, pasta=None, tocar=False, assincrono=False, tts=None, piper_voz=None,
                 length_scale=None, cache_dir=None, pre_cache=False):
        self.motor = motor or config.VOZ                # binário do espeak-ng (fallback)
        self.pasta = Path(pasta or config.VOZ_DIR)
        self.pasta.mkdir(parents=True, exist_ok=True)
        self.tocar = tocar
        self.log = []
        self.tts = (tts or config.TTS).lower()
        self.piper_voz = Path(piper_voz or config.PIPER_VOZ)
        self.length_scale = float(length_scale if length_scale is not None else config.PIPER_LENGTH_SCALE)
        self.cache_dir = Path(cache_dir or config.VOZ_CACHE)
        self.espeak_ok = shutil.which(self.motor) is not None
        self.piper = None
        self.erro_piper = None
        if self.tts == "piper":
            try:
                self.piper = MotorPiper(self.piper_voz, self.length_scale)
            except Exception as e:  # noqa: BLE001 - qualquer falha => fallback espeak
                self.erro_piper = f"{type(e).__name__}: {str(e)[:150]}"
        self.disponivel = self.piper is not None or self.espeak_ok
        self.cache_info = None
        if pre_cache:
            self.cache_info = self.pre_gerar()
        self.assincrono = assincrono
        self._fila: queue.Queue | None = None
        if assincrono:
            self._fila = queue.Queue()
            self._th = threading.Thread(target=self._worker, daemon=True, name="voz")
            self._th.start()

    @property
    def motor_ativo(self):
        return "piper" if self.piper is not None else ("espeak" if self.espeak_ok else None)

    @staticmethod
    def frase(limite_kmh):
        return f"radar, {limite_kmh}" if limite_kmh else "radar adiante"

    # --- cache --------------------------------------------------------------------------
    def _chave(self, texto):
        ident = f"{self.motor_ativo}|{self.piper_voz.name if self.piper else self.motor}|{self.length_scale}|{texto}"
        return hashlib.sha1(ident.encode()).hexdigest()[:16]

    def caminho_cache(self, texto):
        return self.cache_dir / f"{self._chave(texto)}.wav"

    def pre_gerar(self, frases=None):
        """Gera (uma vez) os WAVs das frases comuns. Devolve tempos por frase."""
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        out = {"motor": self.motor_ativo, "frases": 0, "ja_existiam": 0, "ms": []}
        for f in frases or frases_comuns():
            p = self.caminho_cache(f)
            if p.exists():
                out["ja_existiam"] += 1
                continue
            t0 = time.perf_counter()
            self._sintetizar(f, p)
            out["ms"].append(round((time.perf_counter() - t0) * 1000, 1))
            out["frases"] += 1
        return out

    # --- síntese ---------------------------------------------------------------------
    def _espeak(self, texto, wav):
        p = subprocess.run([self.motor, "-v", "pt-br", "-s", "170", "-w", str(wav), texto], capture_output=True, text=True)
        if p.returncode != 0:
            raise RuntimeError(p.stderr.strip()[:200])

    def _sintetizar(self, texto, wav):
        """Piper; se falhar, espeak-ng. Devolve o motor usado."""
        if self.piper is not None:
            try:
                self.piper.sintetizar(texto, wav)
                return "piper"
            except Exception as e:  # noqa: BLE001
                self.erro_piper = f"{type(e).__name__}: {str(e)[:150]}"
        if not self.espeak_ok:
            raise RuntimeError(f"{self.motor} não encontrado")
        self._espeak(texto, wav)
        return "espeak"

    def _tocar(self, wav):
        for cmd in (["aplay", "-q"], ["paplay"], ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet"]):
            if shutil.which(cmd[0]):
                subprocess.run(cmd + [str(wav)], capture_output=True)
                return cmd[0]
        return None

    def _executar(self, reg):
        t0 = time.perf_counter()
        try:
            cache = self.caminho_cache(reg["texto"])
            if cache.exists():
                wav, reg["cache"], reg["motor"] = cache, True, self.motor_ativo
            else:
                wav = self.pasta / f"{int(time.time()*1000)}_{id(reg)}.wav"
                reg["motor"] = self._sintetizar(reg["texto"], wav)
                reg["cache"] = False
            reg["ms"] = round((time.perf_counter() - t0) * 1000, 1)   # até o áudio estar pronto
            reg["wav"] = str(wav)
            if self.tocar:
                reg["player"] = self._tocar(wav)
        except Exception as e:  # noqa: BLE001
            reg["erro"] = f"{type(e).__name__}: {str(e)[:200]}"
            reg["ms"] = round((time.perf_counter() - t0) * 1000, 1)
        return reg

    def _worker(self):
        while True:
            reg = self._fila.get()
            if reg is None:
                break
            while not self._fila.empty():   # descarta falas velhas: fala só a mais recente
                prox = self._fila.get_nowait()
                if prox is None:
                    self._fila.put(None)
                    break
                reg["erro"] = "substituida"
                reg["feito"] = True
                reg = prox
            try:
                self._executar(reg)
            finally:
                reg["feito"] = True

    def falar(self, texto: str, t=None):
        reg = {"t": t, "texto": texto, "wav": None, "erro": None, "ms": None, "feito": False, "motor": None, "cache": None}
        self.log.append(reg)
        if self._fila is not None:
            t0 = time.perf_counter()
            self._fila.put(reg)
            reg["ms_chamada"] = round((time.perf_counter() - t0) * 1000, 3)
            return reg
        self._executar(reg)
        reg["feito"] = True
        return reg

    def esperar(self, timeout=10.0):
        fim = time.time() + timeout
        while time.time() < fim and not all(r.get("feito") for r in self.log):
            time.sleep(0.01)

    def fechar(self):
        if self._fila is not None:
            self._fila.put(None)
