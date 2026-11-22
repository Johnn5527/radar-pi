"""Cruzamento da base de radares com o OpenStreetMap (só VALIDA; OSM nunca é fonte oficial).

Biblioteca padrão apenas (o leitor de .pbf usa `osmium` se estiver instalado).
Categorias por radar da base:
  confirmado_osm  - há radar OSM a <= raio (40 m)
  proximo_osm     - o mais próximo do OSM está entre raio e raio_proximo (150 m): posição duvidosa
  so_base         - nada do OSM por perto (NÃO significa que não existe: só não verificado)
e por radar do OSM:
  so_osm          - nenhum radar da base a <= raio_proximo -> lista de REVISÃO (nunca entra no alerta)
"""
from __future__ import annotations

import json
import math
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path

OVERPASS = ["https://overpass-api.de/api/interpreter",
            "https://overpass.kumi.systems/api/interpreter",
            "https://maps.mail.ru/osm/tools/overpass/api/interpreter"]
UA = "radar-pi/1.0 (validacao local de radares; uso nao comercial)"
MAX_BYTES = 200 * 1024 * 1024          # não aceita resposta gigante/maliciosa
TAGS_RADAR = (("highway", "speed_camera"), ("enforcement", "maxspeed"))


def haversine(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * 6371000.0 * math.asin(math.sqrt(min(1.0, a)))


def parse_maxspeed(v) -> int | None:
    """'60', '60 km/h', '60;40' (pega o 1º), '40 mph' -> km/h. 'none', 'signals', 'BR:urban' -> None."""
    if v is None:
        return None
    m = re.match(r"\s*(\d{1,3})\s*(mph)?", str(v))
    if not m:
        return None
    n = int(m.group(1))
    if m.group(2):
        n = round(n * 1.609)
    return n if 5 <= n <= 150 else None


def coord_valida(lat, lon) -> bool:
    """Bbox do Brasil (com folga). Coordenada fora => descartada do cruzamento e contada."""
    try:
        return -34.5 <= float(lat) <= 5.6 and -74.5 <= float(lon) <= -28.5
    except (TypeError, ValueError):
        return False


# ---------------------------------------------------------------- download
def query_overpass(area: str = "BR", bbox: tuple | None = None, timeout_s: int = 180) -> str:
    """area = 'BR' ou 'BR-SP' (ISO3166). bbox=(s,w,n,e) tem prioridade."""
    if not re.fullmatch(r"BR(-[A-Z]{2})?", area):
        raise ValueError("area inválida")
    if bbox:
        s, w, n, e = (float(x) for x in bbox)
        filt = f"({s},{w},{n},{e})"
        pre = ""
    else:
        key = "ISO3166-1" if area == "BR" else "ISO3166-2"
        pre = f'area["{key}"="{area}"]->.a;'
        filt = "(area.a)"
    sel = "".join(f'node["{k}"="{v}"]{filt};' for k, v in TAGS_RADAR)
    rel = f'relation["enforcement"="maxspeed"]{filt};'
    return (f"[out:json][timeout:{int(timeout_s)}];{pre}({sel}{rel})->.r;"
            f".r out body;node(r.r:\"device\");out body;")


def baixar_overpass(q: str, cache: Path, max_idade_h=24 * 7, tentativas=2, timeout_s=200, log=print) -> dict:
    cache = Path(cache)
    if cache.exists() and time.time() - cache.stat().st_mtime < max_idade_h * 3600:
        return json.loads(cache.read_text())
    erros = []
    for url in OVERPASS:
        for t in range(tentativas):
            try:
                req = urllib.request.Request(url, data=urllib.parse.urlencode({"data": q}).encode(),
                                             headers={"User-Agent": UA, "Accept": "application/json"})
                with urllib.request.urlopen(req, timeout=timeout_s) as r:
                    raw = r.read(MAX_BYTES + 1)
                if len(raw) > MAX_BYTES:
                    raise ValueError("resposta acima do limite")
                d = json.loads(raw)
                if "elements" not in d:
                    raise ValueError(f"resposta sem elements: {str(d)[:200]}")
                if d.get("remark") and "runtime error" in d["remark"]:
                    raise TimeoutError(d["remark"][:200])
                cache.parent.mkdir(parents=True, exist_ok=True)
                tmp = cache.with_suffix(".tmp")
                tmp.write_text(json.dumps(d))
                tmp.replace(cache)                      # escrita atômica
                return d
            except Exception as e:                      # noqa: BLE001 - registra e tenta o próximo
                erros.append(f"{url} #{t + 1}: {type(e).__name__}: {str(e)[:160]}")
                log(erros[-1])
                time.sleep(2 * (t + 1))
    raise ConnectionError("Overpass indisponível: " + " | ".join(erros))


def elementos_overpass(d: dict) -> list[dict]:
    """Nós com as tags de radar + nós 'device' de relações enforcement=maxspeed (herdam maxspeed)."""
    nos = {e["id"]: e for e in d.get("elements", []) if e.get("type") == "node"}
    out, vistos = [], set()
    for e in d.get("elements", []):
        if e.get("type") == "relation":
            tags = e.get("tags", {})
            for m in e.get("members", []):
                if m.get("role") == "device" and m.get("type") == "node" and m.get("ref") in nos:
                    n = nos[m["ref"]]
                    t = dict(tags) | n.get("tags", {})
                    out.append(_osm(n["id"], n["lat"], n["lon"], t))
                    vistos.add(n["id"])
    for n in nos.values():
        t = n.get("tags", {})
        if n["id"] not in vistos and any(t.get(k) == v for k, v in TAGS_RADAR):
            out.append(_osm(n["id"], n["lat"], n["lon"], t))
    return out


def _osm(i, lat, lon, tags):
    return {"osm_id": f"node/{i}", "lat": float(lat), "lon": float(lon),
            "maxspeed": parse_maxspeed(tags.get("maxspeed")), "maxspeed_txt": tags.get("maxspeed"),
            "direction": tags.get("direction"), "nome": tags.get("name") or tags.get("ref")}


def ler_pbf(pbf: Path) -> list[dict]:
    """Fallback Geofabrik: lê nós de radar e nós 'device' de relações enforcement=maxspeed de um .osm.pbf.
    Usa os filtros em C++ do pyosmium (>=4) — passa 3x pelo arquivo, sem índice de nós em memória."""
    import osmium  # opcional
    from osmium.filter import IdFilter, TagFilter

    rel_tags = {}
    fp = osmium.FileProcessor(str(pbf), osmium.osm.RELATION).with_filter(TagFilter(("enforcement", "maxspeed")))
    for r in fp:
        t = {x.k: x.v for x in r.tags}
        for m in r.members:
            if m.role == "device" and m.type == "n":
                rel_tags[m.ref] = t
    out, vistos = [], set()

    def _add(n, extra):
        if n.id in vistos or not n.location.valid():
            return
        vistos.add(n.id)
        out.append(_osm(n.id, n.location.lat, n.location.lon, extra | {x.k: x.v for x in n.tags}))

    for n in osmium.FileProcessor(str(pbf), osmium.osm.NODE).with_filter(
            TagFilter(("highway", "speed_camera"), ("enforcement", "maxspeed"))):
        _add(n, rel_tags.get(n.id, {}))
    if rel_tags:
        for n in osmium.FileProcessor(str(pbf), osmium.osm.NODE).with_filter(IdFilter(list(rel_tags))):
            _add(n, rel_tags[n.id])
    return out


def ler_geojson(path: Path) -> list[dict]:
    d = json.loads(Path(path).read_text())
    out = []
    for f in d.get("features", []):
        g, p = f.get("geometry") or {}, f.get("properties") or {}
        if g.get("type") == "Point":
            lon, lat = g["coordinates"][:2]
            out.append(_osm(p.get("@id") or p.get("id") or f.get("id"), lat, lon, p))
    return out


# ---------------------------------------------------------------- cruzamento
class Grade:
    """Índice espacial simples (células de ~0,002° ≈ 220 m)."""

    def __init__(self, pts, passo=0.002):
        self.p = passo
        self.c = defaultdict(list)
        for x in pts:
            self.c[self._k(x["lat"], x["lon"])].append(x)

    def _k(self, lat, lon):
        return int(math.floor(lat / self.p)), int(math.floor(lon / self.p))

    def perto(self, lat, lon, raio_m):
        i, j = self._k(lat, lon)
        r = int(math.ceil(raio_m / (self.p * 111000 * max(0.2, math.cos(math.radians(lat)))))) + 1
        for di in range(-r, r + 1):
            for dj in range(-r, r + 1):
                for x in self.c.get((i + di, j + dj), ()):
                    d = haversine(lat, lon, x["lat"], x["lon"])
                    if d <= raio_m:
                        yield d, x


def cruzar(base: list[dict], osm: list[dict], raio=40.0, raio_proximo=150.0, tol_limite=0):
    """base: dicts com id, lat, lon, limite (opcional), uf (opcional).
    Retorna (linhas_base, linhas_so_osm, invalidas)."""
    if raio <= 0 or raio_proximo < raio:
        raise ValueError("raios inválidos")
    validos, invalidas = [], []
    for b in base:
        (validos if coord_valida(b.get("lat"), b.get("lon")) else invalidas).append(b)
    for b in validos:
        b["lat"], b["lon"] = float(b["lat"]), float(b["lon"])
    osm = [o for o in osm if coord_valida(o["lat"], o["lon"])]
    g_osm, g_base = Grade(osm), Grade(validos)
    linhas = []
    for b in validos:
        viz = sorted(g_osm.perto(b["lat"], b["lon"], raio_proximo), key=lambda x: x[0])
        d, o = viz[0] if viz else (None, None)
        cat = "so_base" if o is None else ("confirmado_osm" if d <= raio else "proximo_osm")
        lim = _int(b.get("limite"))
        div = bool(o and lim and o["maxspeed"] and abs(lim - o["maxspeed"]) > tol_limite)
        linhas.append({"id": b.get("id"), "uf": b.get("uf"), "lat": b["lat"], "lon": b["lon"], "limite_base": lim,
                       "categoria": cat, "dist_m": round(d, 1) if d is not None else None,
                       "osm_id": o and o["osm_id"], "limite_osm": o and o["maxspeed"],
                       "limite_divergente": div})
    so_osm = []
    for o in osm:
        if not any(True for _ in g_base.perto(o["lat"], o["lon"], raio_proximo)):
            so_osm.append({"id": None, "uf": None, "lat": o["lat"], "lon": o["lon"], "limite_base": None,
                           "categoria": "so_osm", "dist_m": None, "osm_id": o["osm_id"],
                           "limite_osm": o["maxspeed"], "limite_divergente": False})
    return linhas, so_osm, invalidas


def _int(v):
    try:
        return int(float(v)) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def seguro_csv(v):
    """Evita injeção de fórmula ao abrir o CSV no Excel/LibreOffice (só para texto)."""
    if isinstance(v, str) and v[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + v
    return v
