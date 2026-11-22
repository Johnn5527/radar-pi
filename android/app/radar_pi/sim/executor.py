"""Loop principal (o mesmo do Pi): GPS -> HMM -> motor de alerta -> tela/voz."""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from radar_pi.alerta.motor import MotorAlerta, RepositorioRadares
from radar_pi.mapmatch.hmm import MapMatcherHMM
from radar_pi.ui import tela
from radar_pi.voz.tts import Voz


@dataclass
class Quadro:
    t: float
    i: int | None
    via: str
    prob: float
    ambiguo: bool
    sem_gps: bool
    alerta: object
    ms: float


def rodar(malha, con, fixes, voz: Voz | None = None, salvar_png=None, alerta_m=None, sigma_z=10.0):
    mm = MapMatcherHMM(malha, sigma_z=sigma_z)
    repo = RepositorioRadares(con, malha)
    motor = MotorAlerta(malha, repo, alerta_m=alerta_m)
    quadros = []
    for f in fixes:
        t0 = time.perf_counter()
        est = mm.atualizar(f)
        al = motor.passo(est, f)
        if al and al.novo and voz:
            voz.falar(Voz.frase(al.limite_kmh), t=f.t)
        ms = (time.perf_counter() - t0) * 1000
        nome = malha.attr[est.i]["nome_orig"] if est.i is not None else ""
        nome = nome[0] if isinstance(nome, list) else (nome or "")
        quadros.append(Quadro(f.t, est.i, nome, est.prob, est.ambiguo, est.sem_gps, al, ms))
        if salvar_png and salvar_png(f.t, al):
            tela.render(al, f.vel_ms * 3.6, nome, est.sem_gps, bool(al and al.via_incerta), caminho=salvar_png(f.t, al))
    return quadros, mm, repo
