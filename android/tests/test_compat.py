"""APK: as bibliotecas puras (android/compat) e os bancos re-indexados dão o MESMO resultado que o stack real."""
import importlib.util
import json
import math
import random
import subprocess
import sys
from pathlib import Path

import pytest

AQUI = Path(__file__).resolve().parent
COMPAT = AQUI.parent / "compat"
APP = AQUI.parent / "app"


def _mod(nome, rel):
    p = COMPAT / rel
    spec = importlib.util.spec_from_file_location(nome, p, submodule_search_locations=[str(p.parent)])
    m = importlib.util.module_from_spec(spec)
    sys.modules[nome] = m
    spec.loader.exec_module(m)
    return m


def test_pyproj_utm23s_igual_ao_real():
    from pyproj import Transformer
    cp = _mod("compat_pyproj", "pyproj/__init__.py")
    F = Transformer.from_crs("EPSG:4326", "EPSG:31983", always_xy=True)
    rnd = random.Random(1)
    for _ in range(5000):
        lat, lon = rnd.uniform(-25.5, -19.5), rnd.uniform(-53.5, -44)
        x, y = F.transform(lon, lat)
        a, b = cp.fwd(lon, lat)
        assert math.hypot(x - a, y - b) < 0.001
        lo, la = cp.inv(x, y)
        assert abs(lo - lon) < 1e-8 and abs(la - lat) < 1e-8


def test_shapely_linha_igual_ao_real():
    from shapely.geometry import LineString as L, Point as P
    sg = _mod("compat_shapely_geom", "shapely/geometry.py")
    rnd = random.Random(2)
    for _ in range(300):
        pts = [(rnd.uniform(0, 500), rnd.uniform(0, 500)) for _ in range(rnd.randint(2, 8))]
        q = (rnd.uniform(-50, 550), rnd.uniform(-50, 550))
        a, b = L(pts), sg.LineString(pts)
        assert abs(a.length - b.length) < 1e-6
        assert abs(a.distance(P(q)) - b.distance(sg.Point(*q))) < 1e-6
        d = rnd.uniform(-10, a.length + 10)
        pa, pb = a.interpolate(d), b.interpolate(d)
        assert math.hypot(pa.x - pb.x, pa.y - pb.y) < 1e-6
        ia, ib = a.interpolate(a.project(P(q))), b.interpolate(b.project(sg.Point(*q)))
        assert abs(math.hypot(ia.x - q[0], ia.y - q[1]) - math.hypot(ib.x - q[0], ib.y - q[1])) < 1e-6


def test_strtree_mesmos_candidatos():
    from shapely import STRtree as T
    from shapely.geometry import LineString as L, box
    sg = _mod("compat_shapely_geom2", "shapely/geometry.py")
    st = _mod("compat_shapely_tree", "shapely/strtree.py")
    rnd = random.Random(3)
    lin = []
    for _ in range(400):
        x, y = rnd.uniform(0, 3000), rnd.uniform(0, 3000)
        lin.append([(x, y), (x + rnd.uniform(-300, 300), y + rnd.uniform(-300, 300))])
    ta, tb = T([L(p) for p in lin]), st.STRtree([sg.LineString(p) for p in lin])
    for _ in range(200):
        x, y, r = rnd.uniform(0, 3000), rnd.uniform(0, 3000), rnd.uniform(10, 600)
        assert sorted(int(i) for i in ta.query(box(x - r, y - r, x + r, y + r))) == \
            sorted(tb.query(sg.box(x - r, y - r, x + r, y + r)))


def _malha_apk_nacional():
    import sqlite3
    p = APP / "data/osm/malha_sp.sqlite"
    if not p.exists():
        return False
    r = sqlite3.connect(p).execute("SELECT valor FROM meta WHERE chave='cobertura'").fetchone()
    return bool(r and r[0].startswith("brasil"))


@pytest.mark.skipif(not (APP / "data/sim/ordem.json").exists() or _malha_apk_nacional(),
                    reason="APK com malha nacional (grafo diferente do da box); coberto por test_sim_gps")
def test_cenarios_mesmo_alerta_e_voz():
    def rodar(modo):
        p = subprocess.run([sys.executable, str(AQUI / "_rodar_cenarios.py"), modo], capture_output=True, text=True,
                           timeout=600)
        assert p.returncode == 0, p.stderr[-2000:]
        return json.loads(p.stdout.strip().splitlines()[-1])
    real, comp = rodar("real"), rodar("compat")
    assert real.keys() == comp.keys()
    for nome in real:
        assert real[nome]["voz"] == comp[nome]["voz"], nome
        for a, b in zip(real[nome]["linhas"], comp[nome]["linhas"]):
            assert a["alerta"] == b["alerta"] and a["placa"] == b["placa"] and a["via"] == b["via"], (nome, a, b)
            assert a["lim_via"] == b["lim_via"], (nome, a, b)
            if a["x"] is not None:
                assert abs(a["x"] - b["x"]) < 0.5, (nome, a, b)


@pytest.mark.skipif(not (APP / "data/sim/br116_dnit.json").exists(), reason="rode android/preparar.py e gerar_sim_br.py")
def test_sim_gps_location():
    p = subprocess.run([sys.executable, str(AQUI / "sim_gps.py")], capture_output=True, text=True, timeout=600)
    assert p.returncode == 0 and p.stdout.strip().endswith("OK"), (p.stdout[-2000:], p.stderr[-2000:])
