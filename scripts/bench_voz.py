"""Mede a síntese do Piper (carga do modelo, ms por frase, fator de tempo real) e do espeak-ng."""
import json
import os
import statistics as st
import sys
import tempfile
import time
import wave
from pathlib import Path

from radar_pi.voz.tts import Voz, frases_comuns

out = {"cpus_visiveis": len(os.sched_getaffinity(0))}
tmp = Path(tempfile.mkdtemp())
for tts in ("piper", "espeak"):
    t0 = time.perf_counter()
    v = Voz(pasta=tmp, tts=tts, cache_dir=tmp / f"c_{tts}")
    carga = (time.perf_counter() - t0) * 1000
    v.falar("aquecimento")
    ms, rtf = [], []
    for f in frases_comuns():
        r = v.falar(f + " ")   # espaço: não pega cache
        with wave.open(r["wav"]) as w:
            d = w.getnframes() / w.getframerate()
        ms.append(r["ms"])
        rtf.append(r["ms"] / 1000 / d)
    out[tts] = {"carga_ms": round(carga), "ms_mediana": round(st.median(ms), 1), "ms_max": round(max(ms), 1),
                "rtf_mediana": round(st.median(rtf), 3)}
print(json.dumps(out))
if len(sys.argv) > 1:
    with open(sys.argv[1], "a") as f:
        f.write(json.dumps(out) + "\n")
