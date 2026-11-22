"""Monta android/app/ (fonte do APK): código radar_pi + substitutos puros (compat/) + dados pré-processados.

Rodar na box:  PYTHONPATH=. .venv/bin/python android/preparar.py
- malha OSM (tiles) e banco de radares copiados e RE-INDEXADOS com a grade do compat/h3 (o h3 nativo não vai no APK);
- trajetos da simulação gerados aqui com o código real (o APK só reproduz os fixes);
- frases da voz sintetizadas aqui com o Piper (OHF-Voice/piper1-gpl, pt_BR-faber-medium) em WAV.
Não leva .env nem segredo nenhum."""
from __future__ import annotations

import importlib.util
import json
import shutil
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AND = ROOT / "android"
APP = AND / "app"
DATA = APP / "data"


def _compat_h3():
    p = AND / "compat" / "h3" / "__init__.py"
    spec = importlib.util.spec_from_file_location("compat_h3", p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def copiar_codigo():
    dst = APP / "radar_pi"
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(ROOT / "radar_pi", dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    c = APP / "compat"
    if c.exists():
        shutil.rmtree(c)
    shutil.copytree(AND / "compat", c, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copy(AND / "main.py", APP / "main.py")
    shutil.copytree(AND / "android_rt", APP / "android_rt", dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    f = APP / "fonts"
    f.mkdir(exist_ok=True)
    for n in ("DejaVuSans.ttf", "DejaVuSans-Bold.ttf"):
        shutil.copy(f"/usr/share/fonts/truetype/dejavu/{n}", f / n)


MALHA_BR = ROOT / "data/osm/estado/malha_brasil_apk.sqlite"   # android/construir_malha_brasil.py


def malha_brasil():
    dst = DATA / "osm/malha_sp.sqlite"
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(MALHA_BR, dst)
    return round(dst.stat().st_size / 1e6, 1)


def vinculos():
    import subprocess
    p = subprocess.run([sys.executable, str(AND / "vincular_apk.py")], capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(p.stderr[-3000:])
    return p.stdout.strip()


def malha(h3c):
    from radar_pi.mapa.grafo import to_latlon
    from radar_pi.mapa.tiles import _blob_xy
    from shapely.geometry import LineString
    src = ROOT / "data/osm/malha_sp.sqlite"
    dst = DATA / "osm/malha_sp.sqlite"
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        dst.unlink()
    shutil.copy(src, dst)
    con = sqlite3.connect(dst)
    res = int(con.execute("SELECT valor FROM meta WHERE chave='res'").fetchone()[0])
    con.execute("DELETE FROM aresta_cel")
    lote = []
    for eid, xy in con.execute("SELECT id, xy FROM arestas").fetchall():
        coords = _blob_xy(xy)
        ls = LineString(coords)
        pts = coords + [(ls.interpolate(d).x, ls.interpolate(d).y) for d in range(500, int(ls.length), 500)]
        for c in {h3c.latlng_to_cell(*to_latlon(x, y), res) for x, y in pts}:
            lote.append((c, eid))
    con.executemany("INSERT INTO aresta_cel VALUES(?,?)", lote)
    con.execute("INSERT OR REPLACE INTO meta VALUES('indice', 'compat_grade_quadrada')")
    con.commit()
    con.execute("VACUUM")
    con.close()
    return len(lote)


def dnit_nacional(con):
    """PNCV do DNIT (fonte oficial federal) SEM o filtro de UF: radares das BRs do país todo.
    O PNCV não traz o limite de velocidade; quando não houver outra fonte, o app usa o maxspeed OSM do trecho
    vinculado (como na placa '60*') e a voz diz "radar adiante"."""
    import datetime as dt
    from radar_pi.coletor.fontes import DNIT
    from radar_pi.dados import db
    bruto = sorted((ROOT / "data/raw/dnit").glob("*__pncv.xlsx"))[-1]
    col = DNIT(raw_dir=ROOT / "data/raw", offline=True)
    col.uf = None
    rs = col.parse(bruto)
    ja = {r[0] for r in con.execute("SELECT id FROM radares")}
    agora = dt.datetime.now().isoformat(timespec="seconds")
    novos = []
    for r in rs:
        d = r.as_row()
        if d["id"] in ja or d["lat"] is None:
            continue
        d["data_coleta"] = agora
        d["coord_origem"] = "fonte"
        d["status"] = d.get("status") or "ativo"
        novos.append(d)
    db.gravar_radares(con, novos)
    con.execute("UPDATE radares SET status_validacao='nao_verificado', n_fontes=1, fontes_validacao='dnit' "
                "WHERE fonte='dnit' AND status_validacao IS NULL")
    con.commit()
    return len(novos)


def radares(h3c):
    from radar_pi import config
    dst = DATA / "radares.sqlite"
    if dst.exists():
        dst.unlink()
    shutil.copy(ROOT / "data/radares.sqlite", dst)
    con = sqlite3.connect(dst)
    from radar_pi.dados import db
    con.close()
    con = db.conectar(dst)
    print("DNIT PNCV nacional (novos):", dnit_nacional(con))
    rows = con.execute("SELECT id, lat, lon FROM radares").fetchall()
    con.executemany("UPDATE radares SET h3=? WHERE id=?",
                    [(h3c.latlng_to_cell(la, lo, config.H3_RES) if la is not None else None, i) for i, la, lo in rows])
    con.commit()
    con.execute("VACUUM")
    con.close()
    return len(rows)


CENARIOS_SIM = ["marginal", "paulista", "cet_acima", "radial_leste", "tamoios_piper", "sem_radar"]


def trajetos():
    from radar_pi.dados import db
    from radar_pi.mapa.tiles import MalhaTiles
    from radar_pi.mapa_ui.cenarios import trajeto
    from radar_pi import config
    out = DATA / "sim"
    out.mkdir(parents=True, exist_ok=True)
    resumo = {}
    for nome in CENARIOS_SIM:
        m = MalhaTiles(config.GRAPH_TILES, "sp")
        con = db.conectar()
        fixes, info = trajeto(nome, m, con, seed=1)
        (out / f"{nome}.json").write_text(json.dumps(
            {"cenario": nome, "info": info,
             "fixes": [[f.t, f.lat, f.lon, f.vel_ms, f.rumo, f.precisao_m] for f in fixes]}))
        resumo[nome] = {"fixes": len(fixes), **info}
    (out / "ordem.json").write_text(json.dumps(CENARIOS_SIM))
    return resumo


def vozes():
    from radar_pi.voz.tts import MotorPiper, Voz, frases_comuns
    from radar_pi import config
    out = DATA / "voz"
    out.mkdir(parents=True, exist_ok=True)
    p = MotorPiper(config.PIPER_VOZ, config.PIPER_LENGTH_SCALE)
    idx = {}
    for k, f in enumerate(frases_comuns() + [Voz.frase(v) for v in (15, 25, 35, 45, 55, 65, 75, 85, 95, 105, 115)]):
        nome = f"f{k:02d}.wav"
        p.sintetizar(f, out / nome)
        idx[f] = nome
    (out / "indice.json").write_text(json.dumps(idx, ensure_ascii=False, indent=1))
    return len(idx)


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))
    APP.mkdir(exist_ok=True)
    so = sys.argv[1:] or ["codigo", "malha_brasil", "radares", "vinculos", "trajetos", "vozes"]
    h3c = _compat_h3()
    if "codigo" in so:
        copiar_codigo(); print("código ok")
    if "malha" in so:
        print("malha aresta_cel:", malha(h3c))
    if "malha_brasil" in so:
        print("malha nacional (MB):", malha_brasil())
    if "radares" in so:
        print("radares:", radares(h3c))
    if "vinculos" in so:
        print(vinculos())
    if "trajetos" in so:
        print("trajetos:", json.dumps(trajetos(), ensure_ascii=False))
    if "vozes" in so:
        print("vozes:", vozes())
