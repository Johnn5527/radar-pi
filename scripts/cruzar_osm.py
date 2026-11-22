#!/usr/bin/env python3
"""Cruza uma base de radares (CSV lat/lon) com os radares do OpenStreetMap.

  python scripts/cruzar_osm.py base.csv [--uf SP | --bbox s,w,n,e] [--raio 40] [--raio-proximo 150]
  python scripts/cruzar_osm.py --exportar-db base.csv          # gera o CSV a partir do SQLite
  python scripts/cruzar_osm.py base.csv --pbf sudeste-latest.osm.pbf   # fallback Geofabrik (sem Overpass)

Saída: divergencias.csv (categoria: confirmado_osm / proximo_osm / so_base / so_osm; limite_divergente).
OSM só valida — nunca é fonte oficial. 'so_base' = não verificado, NÃO 'não existe'.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from radar_pi.validacao import osm as V  # noqa: E402

LAT = ("lat", "latitude", "y")
LON = ("lon", "lng", "long", "longitude", "x")
CAMPOS = ["categoria", "id", "uf", "lat", "lon", "limite_base", "osm_id", "limite_osm", "dist_m", "limite_divergente"]


def ler_csv(path: Path) -> list[dict]:
    txt = path.read_text(encoding="utf-8-sig")
    dial = ";" if txt.splitlines()[0].count(";") > txt.splitlines()[0].count(",") else ","
    rows = list(csv.DictReader(txt.splitlines(), delimiter=dial))
    if not rows:
        return []
    cols = {c.lower().strip(): c for c in rows[0].keys()}
    clat = next((cols[c] for c in LAT if c in cols), None)
    clon = next((cols[c] for c in LON if c in cols), None)
    if not clat or not clon:
        raise SystemExit(f"CSV sem colunas de latitude/longitude: {list(rows[0])}")
    out = []
    for r in rows:
        f = lambda v: (v or "").strip().replace(",", ".")  # noqa: E731 - aceita vírgula decimal
        out.append({"id": r.get(cols.get("id", ""), None), "uf": r.get(cols.get("uf", ""), None),
                    "limite": r.get(cols.get("limite", ""), None), "lat": f(r[clat]), "lon": f(r[clon])})
    return out


def exportar_db(saida: Path, db_path=None):
    from radar_pi.dados import db
    con = db.conectar(db_path)
    from radar_pi.validacao.uf import uf_de
    rows = con.execute("SELECT id, lat, lon, limite_kmh AS limite FROM radares "
                       "WHERE status='ativo' AND duplicata_de IS NULL AND lat IS NOT NULL ORDER BY id").fetchall()
    with open(saida, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["id", "lat", "lon", "limite", "uf"])
        w.writerows([V.seguro_csv(r["id"]), r["lat"], r["lon"], r["limite"], uf_de(r["lat"], r["lon"]) or ""] for r in rows)
    return len(rows)


def obter_osm(a) -> tuple[list[dict], str]:
    if a.pbf:
        return V.ler_pbf(Path(a.pbf)), f"geofabrik:{Path(a.pbf).name}"
    if a.geojson:
        return V.ler_geojson(Path(a.geojson)), f"geojson:{Path(a.geojson).name}"
    bbox = tuple(a.bbox.split(",")) if a.bbox else None
    area = f"BR-{a.uf.upper()}" if a.uf else "BR"
    cache = Path(a.cache) / f"overpass_{(a.bbox or area).replace(',', '_')}.json"
    d = V.baixar_overpass(V.query_overpass(area, bbox), cache, log=lambda m: print("  overpass:", m, file=sys.stderr))
    return V.elementos_overpass(d), f"overpass:{area if not bbox else a.bbox}"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("base", type=Path)
    ap.add_argument("--exportar-db", action="store_true", help="só exporta o SQLite para o CSV 'base'")
    ap.add_argument("--raio", type=float, default=40.0)
    ap.add_argument("--raio-proximo", type=float, default=150.0)
    ap.add_argument("--uf", help="limita o Overpass a uma UF (ex.: SP)")
    ap.add_argument("--bbox", help="s,w,n,e")
    ap.add_argument("--pbf", help="extrato Geofabrik .osm.pbf (fallback sem Overpass)")
    ap.add_argument("--geojson", help="GeoJSON de pontos já extraído")
    ap.add_argument("--cache", default="data/raw/osm")
    ap.add_argument("--saida", type=Path, default=Path("divergencias.csv"))
    ap.add_argument("--salvar-osm", type=Path, help="grava os radares OSM normalizados (usado pelo coletor)")
    ap.add_argument("--aplicar-db", action="store_true", help="grava status_validacao/n_fontes/revisao_osm no SQLite")
    a = ap.parse_args(argv)
    if a.exportar_db:
        print(f"{exportar_db(a.base)} radares exportados -> {a.base}")
        return 0
    base = ler_csv(a.base)
    osm, origem = obter_osm(a)
    linhas, so_osm, invalidas = V.cruzar(base, osm, a.raio, a.raio_proximo)
    with open(a.saida, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, CAMPOS, extrasaction="ignore")
        w.writeheader()
        for r in linhas + so_osm:
            w.writerow({k: V.seguro_csv(v) for k, v in r.items()})
    cat = Counter(r["categoria"] for r in linhas + so_osm)
    from radar_pi.validacao.uf import uf_de
    for r in so_osm:
        r["uf"] = uf_de(r["lat"], r["lon"])
    por_uf = defaultdict(Counter)
    for r in linhas + so_osm:
        por_uf[r["uf"] or "?"][r["categoria"]] += 1
    resumo = {"origem_osm": origem, "radares_osm": len(osm), "base": len(base), "coord_invalidas": len(invalidas),
              "categorias": dict(cat), "limite_divergente": sum(r["limite_divergente"] for r in linhas),
              "limite_comparavel": sum(1 for r in linhas if r["limite_base"] and r["limite_osm"]),
              "por_uf": {k: dict(v) for k, v in por_uf.items()}, "saida": str(a.saida)}
    if a.salvar_osm:
        a.salvar_osm.parent.mkdir(parents=True, exist_ok=True)
        a.salvar_osm.write_text(json.dumps({"origem": origem, "radares": osm}, ensure_ascii=False))
    if a.aplicar_db:
        from radar_pi.dados import db
        from radar_pi.validacao import status as val
        resumo["validacao_db"] = val.aplicar(db.conectar(), osm, origem, a.raio, a.raio_proximo)
    print(json.dumps(resumo, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
