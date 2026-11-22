"""Módulo de voz: Piper TTS (API Python), cache das frases comuns, fallback espeak-ng e fila não bloqueante."""
import wave

import pytest

from radar_pi import config
from radar_pi.voz.tts import Voz, frases_comuns

pytestmark = pytest.mark.skipif(not config.PIPER_VOZ.exists(), reason="voz Piper não baixada")


def _dur(p):
    with wave.open(str(p)) as w:
        return w.getnframes() / w.getframerate(), w.getframerate()


def test_piper_sintetiza_ptbr(tmp_path, registrar):
    v = Voz(pasta=tmp_path, tts="piper", cache_dir=tmp_path / "c")
    r = v.falar("radar, 60")
    d, sr = _dur(r["wav"])
    registrar(cenario="piper_sintese", carga_modelo_ms=v.piper.ms_carga, ms=r["ms"], audio_s=round(d, 2), sample_rate=sr)
    assert v.motor_ativo == "piper" and r["motor"] == "piper" and not r["erro"] and 0.5 < d < 3.0 and sr == 22050


def test_cache_frases_comuns_latencia(tmp_path, registrar):
    v = Voz(pasta=tmp_path, tts="piper", cache_dir=tmp_path / "c")
    info = v.pre_gerar()
    assert info["frases"] == len(frases_comuns()) == 12 and "radar adiante" in frases_comuns()
    assert v.pre_gerar()["ja_existiam"] == 12            # 2ª vez não sintetiza de novo
    r = v.falar("radar, 80")
    registrar(cenario="piper_cache", sintese_ms=info["ms"], ms_media=round(sum(info["ms"]) / len(info["ms"]), 1),
              ms_max=max(info["ms"]), ms_com_cache=r["ms"])
    assert r["cache"] is True and r["ms"] < 5


def test_fallback_espeak_sem_voz(tmp_path):
    v = Voz(pasta=tmp_path, tts="piper", piper_voz=tmp_path / "nao_existe.onnx", cache_dir=tmp_path / "c")
    r = v.falar("radar, 50")
    assert v.motor_ativo == "espeak" and v.erro_piper and r["motor"] == "espeak" and r["wav"] and not r["erro"]


def test_fallback_espeak_erro_na_sintese(tmp_path, monkeypatch):
    v = Voz(pasta=tmp_path, tts="piper", cache_dir=tmp_path / "c")

    def quebra(*a, **k):
        raise RuntimeError("onnx falhou")
    monkeypatch.setattr(v.piper, "sintetizar", quebra)
    r = v.falar("radar, 70")
    assert r["motor"] == "espeak" and r["wav"] and "onnx falhou" in v.erro_piper


def test_tts_espeak_por_config(tmp_path):
    v = Voz(pasta=tmp_path, tts="espeak", cache_dir=tmp_path / "c")
    assert v.piper is None and v.falar("radar, 40")["motor"] == "espeak"


def test_fila_nao_bloqueia_e_fala_so_a_ultima(tmp_path, registrar):
    v = Voz(pasta=tmp_path, tts="piper", cache_dir=tmp_path / "c", assincrono=True)
    regs = [v.falar(f"radar, {x}") for x in (40, 50, 60, 70)]
    v.esperar(20)
    v.fechar()
    feitos = [r for r in regs if r["wav"]]
    registrar(cenario="piper_fila", ms_chamada_max=max(r["ms_chamada"] for r in regs), faladas=len(feitos))
    assert max(r["ms_chamada"] for r in regs) < 5
    assert regs[-1]["wav"] and regs[-1]["motor"] == "piper"
