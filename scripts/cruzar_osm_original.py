#!/usr/bin/env python3
"""
Cruza a sua base de radares (Brasil inteiro) com os radares do OpenStreetMap.

Uso:
    python cruzar_osm.py minha_base.csv
    python cruzar_osm.py minha_base.csv --raio 40 --raio-proximo 150 --saida divergencias.csv

A base precisa de um CSV com colunas de latitude e longitude (nomes aceitos:
lat/latitude/y e lon/lng/long/longitude/x). Colunas opcionais: id, limite, uf.

Só usa a biblioteca padrão do Python (sem pip install).

Status gerados (nunca existe "não tem radar"):
    confirmado_osm : radar da base com um radar do OSM a <= --raio metros
    proximo_osm    : radar da base com OSM entre --raio e --raio-proximo (posição diferente, revisar)
    so_base        : radar da base sem nada no OSM por perto (NÃO VERIFICADO; o OSM é incompleto)
    so_osm         : radar do OSM sem nada na base por perto (candidato a radar novo)
"""
import argparse
import csv
import json
import math
import re
import sys
import time
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

OVERPASS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]

QUERY = """
[out:json][timeout:600];
area["ISO3166-1"="BR"]["admin_level"="2"]->.br;
(
  node["highway"="speed_camera"](area.br);
  node["enforcement"="maxspeed"](area.br);
);
out body;
"""

CELL = 0.002  # ~220 m de latitude; grade para busca de vizinhos sem dependências


def haversine(lat1, lon1, lat2, lon2):
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


class Grade:
    def __init__(self):
        self.celulas = defaultdict(list)

    @staticmethod
    def chave(lat, lon):
        return (int(math.floor(lat / CELL)), int(math.floor(lon / CELL)))

    def add(self, idx, lat, lon):
        self.celulas[self.chave(lat, lon)].append((idx, lat, lon))

    def perto(self, lat, lon, raio):
        """Lista [(distancia_m, idx)] dentro de 'raio' metros, do mais perto ao mais longe."""
        alcance = max(1, math.ceil(raio / 190.0))
        cx, cy = self.chave(lat, lon)
        achados = []
        for dx in range(-alcance, alcance + 1):
            for dy in range(-alcance, alcance + 1):
                for idx, la, lo in self.celulas.get((cx + dx, cy + dy), ()):
                    d = haversine(lat, lon, la, lo)
                    if d <= raio:
                        achados.append((d, idx))
        achados.sort()
        return achados


def para_float(valor):
    if valor is None:
        return None
    try:
        return float(str(valor).strip().replace(",", "."))
    except ValueError:
        return None


def para_limite(valor):
    """'60', '60 km/h', '90/60' -> primeiro número inteiro; senão None."""
    if valor is None:
        return None
    m = re.search(r"\d+", str(valor))
    return int(m.group()) if m else None


def achar_coluna(colunas, nomes):
    baixo = {c.lower().strip(): c for c in colunas}
    for n in nomes:
        if n in baixo:
            return baixo[n]
    return None


def ler_base(caminho):
    texto = Path(caminho).read_text(encoding="utf-8-sig")
    primeira = texto.splitlines()[0] if texto else ""
    delim = ";" if primeira.count(";") > primeira.count(",") else ","
    leitor = csv.DictReader(texto.splitlines(), delimiter=delim)
    cols = leitor.fieldnames or []
    c_lat = achar_coluna(cols, ["lat", "latitude", "y"])
    c_lon = achar_coluna(cols, ["lon", "lng", "long", "longitude", "x"])
    if not c_lat or not c_lon:
        sys.exit(f"Não achei colunas de latitude/longitude. Colunas: {cols}")
    c_id = achar_coluna(cols, ["id", "codigo", "cod"])
    c_lim = achar_coluna(cols, ["limite", "maxspeed", "velocidade", "limite_kmh"])
    c_uf = achar_coluna(cols, ["uf", "estado"])
    base = []
    for n, linha in enumerate(leitor, start=1):
        lat, lon = para_float(linha.get(c_lat)), para_float(linha.get(c_lon))
        if lat is None or lon is None or not (-35 <= lat <= 6 and -75 <= lon <= -28):
            continue  # fora do Brasil ou inválida
        base.append({
            "id": linha.get(c_id) if c_id else str(n),
            "lat": lat,
            "lon": lon,
            "limite": para_limite(linha.get(c_lim)) if c_lim else None,
            "uf": (linha.get(c_uf) or "").strip().upper() if c_uf else "",
        })
    return base


def baixar_osm(cache, atualizar):
    cache = Path(cache)
    if cache.exists() and not atualizar:
        print(f"Usando cache do OSM: {cache}")
        return json.loads(cache.read_text(encoding="utf-8"))
    dados = urllib.parse.urlencode({"data": QUERY}).encode()
    ultimo_erro = None
    for tentativa in range(3):
        for url in OVERPASS:
            try:
                print(f"Baixando radares do OSM (Brasil) de {url} ...")
                req = urllib.request.Request(url, data=dados, headers={"User-Agent": "radar-pi-cruzamento/1.0"})
                with urllib.request.urlopen(req, timeout=700) as r:
                    corpo = json.loads(r.read().decode("utf-8"))
                cache.write_text(json.dumps(corpo), encoding="utf-8")
                return corpo
            except Exception as e:  # noqa: BLE001
                ultimo_erro = e
                print(f"  falhou: {e}")
        time.sleep(10 * (tentativa + 1))
    sys.exit(f"Não consegui baixar do Overpass ({ultimo_erro}). "
             "Veja o README no final deste arquivo para a alternativa com Geofabrik.")


def extrair_osm(corpo):
    vistos, osm = set(), []
    for el in corpo.get("elements", []):
        if el.get("type") != "node" or "lat" not in el or el["id"] in vistos:
            continue
        vistos.add(el["id"])
        tags = el.get("tags", {})
        osm.append({
            "id": el["id"],
            "lat": el["lat"],
            "lon": el["lon"],
            "limite": para_limite(tags.get("maxspeed")),
            "nome": tags.get("name") or tags.get("ref") or "",
        })
    return osm


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("base", help="CSV com a sua base de radares")
    ap.add_argument("--raio", type=float, default=40, help="metros para considerar o mesmo radar (padrão 40)")
    ap.add_argument("--raio-proximo", type=float, default=150, help="metros para 'proximo_osm' (padrão 150)")
    ap.add_argument("--cache", default="osm_radares_br.json", help="arquivo de cache do OSM")
    ap.add_argument("--atualizar", action="store_true", help="baixa o OSM de novo, ignorando o cache")
    ap.add_argument("--saida", default="divergencias.csv")
    args = ap.parse_args()
    if args.raio_proximo < args.raio:
        sys.exit("--raio-proximo precisa ser >= --raio")

    base = ler_base(args.base)
    osm = extrair_osm(baixar_osm(args.cache, args.atualizar))
    print(f"Base: {len(base)} radares | OSM: {len(osm)} radares no Brasil")

    g_osm, g_base = Grade(), Grade()
    for i, o in enumerate(osm):
        g_osm.add(i, o["lat"], o["lon"])
    for i, b in enumerate(base):
        g_base.add(i, b["lat"], b["lon"])

    linhas = []
    por_uf = defaultdict(Counter)
    for b in base:
        viz = g_osm.perto(b["lat"], b["lon"], args.raio_proximo)
        if viz and viz[0][0] <= args.raio:
            status = "confirmado_osm"
        elif viz:
            status = "proximo_osm"
        else:
            status = "so_base"
        d, o = (viz[0][0], osm[viz[0][1]]) if viz else (None, None)
        diverge = bool(o and b["limite"] and o["limite"] and b["limite"] != o["limite"])
        linhas.append({
            "origem": "base", "id": b["id"], "lat": b["lat"], "lon": b["lon"], "status": status,
            "osm_id": o["id"] if o else "", "dist_m": round(d, 1) if d is not None else "",
            "limite_base": b["limite"] or "", "limite_osm": (o["limite"] or "") if o else "",
            "limite_diverge": "sim" if diverge else "", "uf": b["uf"],
        })
        por_uf[b["uf"] or "?"][status] += 1

    for o in osm:
        if not g_base.perto(o["lat"], o["lon"], args.raio_proximo):
            linhas.append({
                "origem": "osm", "id": o["id"], "lat": o["lat"], "lon": o["lon"], "status": "so_osm",
                "osm_id": o["id"], "dist_m": "", "limite_base": "", "limite_osm": o["limite"] or "",
                "limite_diverge": "", "uf": "",
            })

    campos = ["origem", "id", "lat", "lon", "status", "osm_id", "dist_m",
              "limite_base", "limite_osm", "limite_diverge", "uf"]
    with open(args.saida, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=campos)
        w.writeheader()
        w.writerows(linhas)

    total = Counter(l["status"] for l in linhas)
    print("\nResumo geral:")
    for s in ("confirmado_osm", "proximo_osm", "so_base", "so_osm"):
        print(f"  {s:15s} {total.get(s, 0)}")
    print(f"  limite divergente: {sum(1 for l in linhas if l['limite_diverge'])}")
    if any(k != "?" for k in por_uf):
        print("\nPor UF (radares da base):")
        for uf in sorted(por_uf):
            c = por_uf[uf]
            n = sum(c.values())
            print(f"  {uf:3s} total {n:5d} | confirmado {c['confirmado_osm']:5d} | "
                  f"proximo {c['proximo_osm']:4d} | so_base {c['so_base']:5d}")
    print(f"\nArquivo gerado: {args.saida}")


if __name__ == "__main__":
    main()

# ---------------------------------------------------------------------------
# README rápido
# - Overpass pode dar timeout para o Brasil inteiro. Se acontecer, use o extrato
#   do Geofabrik (brazil-latest.osm.pbf) com o osmium-tool:
#     osmium tags-filter brazil-latest.osm.pbf n/highway=speed_camera n/enforcement=maxspeed -o radares.osm.pbf
#     osmium export radares.osm.pbf -f geojson -o radares.geojson
#   e adapte baixar_osm()/extrair_osm() para ler o GeoJSON.
# - so_base NÃO significa "radar não existe": o OSM tem cobertura incompleta.
# - so_osm são candidatos a radar novo ou antigo/desativado: confira antes de adicionar.
# ---------------------------------------------------------------------------
