"""Executa todos os coletores, cruza fontes, marca duplicatas e grava o SQLite."""
from __future__ import annotations

import json

import math
from pathlib import Path
from collections import defaultdict

from radar_pi.dados import db
from .fontes import TODOS, PRIORIDADE


def haversine(lat1, lon1, lat2, lon2):
    R = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def marcar_duplicatas(rows, raio_m=30.0):
    """Mesma posição (<raio) em fontes DIFERENTES => duplicata; mantém a fonte de maior prioridade."""
    import h3
    por_cel = defaultdict(list)
    for r in rows:
        if r.get("lat") is not None and r["status"] == "ativo":
            por_cel[h3.latlng_to_cell(r["lat"], r["lon"], 10)].append(r)
    pares = []
    for r in rows:
        if r.get("lat") is None or r["status"] != "ativo":
            continue
        for c in h3.grid_disk(h3.latlng_to_cell(r["lat"], r["lon"], 10), 1):
            for o in por_cel.get(c, []):
                if o is r or o["fonte"] == r["fonte"] or o.get("duplicata_de"):
                    continue
                if haversine(r["lat"], r["lon"], o["lat"], o["lon"]) <= raio_m:
                    a, b = sorted([r, o], key=lambda x: PRIORIDADE.get(x["fonte"], 9))
                    if not b.get("duplicata_de"):
                        b["duplicata_de"] = a["id"]
                        pares.append((a["fonte"], b["fonte"]))
    return pares


def dedup_mesma_fonte(rows, raio_m=5.0):
    """E10: registros da MESMA fonte no mesmo ponto (<=5 m), ambos ativos => mantém 1."""
    import h3
    por_cel = defaultdict(list)
    n = 0
    for r in rows:
        if r.get("lat") is None or r["status"] != "ativo" or r.get("duplicata_de"):
            continue
        c = h3.latlng_to_cell(r["lat"], r["lon"], 12)
        for o in por_cel[c]:
            if o["fonte"] == r["fonte"] and haversine(r["lat"], r["lon"], o["lat"], o["lon"]) <= raio_m:
                r["duplicata_de"] = o["id"]
                n += 1
                break
        else:
            por_cel[c].append(r)
    return n


def cruzar_espelho(rows, espelho, raio_m=40.0):
    """E1: limite do Inmetro via espelho (Pardal) por proximidade, só onde a fonte não tem limite."""
    import h3
    idx = defaultdict(list)
    for e in espelho:
        if e["lat"] is not None and e["status"] == "ativo":
            idx[h3.latlng_to_cell(e["lat"], e["lon"], 10)].append(e)
    n = 0
    for r in rows:
        if r.get("limite_kmh") or r.get("lat") is None:
            continue
        best, bd = None, raio_m
        for c in h3.grid_disk(h3.latlng_to_cell(r["lat"], r["lon"], 10), 1):
            for e in idx.get(c, []):
                d = haversine(r["lat"], r["lon"], e["lat"], e["lon"])
                if d <= bd:
                    best, bd = e, d
        if best:
            r["limite_kmh"], r["limite_texto"], r["limite_origem"] = best["limite_kmh"], best["limite_texto"], "inmetro_via_espelho"
            n += 1
    return n


def adicionar_antt_espelho(rows, espelho, raio_m=40.0):
    """E7: ANTT fora do ar a partir daqui => radares ANTT (concessões federais) vindos do espelho,
    só os que não coincidem com nenhum radar já conhecido. Marcados fonte='antt_via_espelho'."""
    n = 0
    conhecidos = [(r["lat"], r["lon"]) for r in rows if r.get("lat") is not None]
    import h3
    idx = defaultdict(list)
    for la, lo in conhecidos:
        idx[h3.latlng_to_cell(la, lo, 10)].append((la, lo))
    for e in espelho:
        if "ANTT" not in (e["extra"] or {}).get("source", "") or e["lat"] is None:
            continue
        perto = any(haversine(e["lat"], e["lon"], la, lo) <= raio_m
                    for c in h3.grid_disk(h3.latlng_to_cell(e["lat"], e["lon"], 10), 1) for la, lo in idx.get(c, []))
        if perto:
            continue
        rows.append({"id": f"antt_via_espelho:{e['id_fonte']}", "fonte": "antt_via_espelho", "id_fonte": e["id_fonte"],
                     "lat": e["lat"], "lon": e["lon"], "limite_kmh": e["limite_kmh"], "limite_texto": e["limite_texto"],
                     "limite_origem": "antt_via_espelho" if e["limite_kmh"] else None, "sentido": e["sentido"],
                     "via": None, "km": None, "municipio": None, "descricao": None, "tipo": e["tipo"], "status": e["status"],
                     "faixas": None, "data_ativacao": None, "data_coleta": e.get("data_coleta"), "extra": e["extra"]})
        n += 1
    return n


def geocodificar_km(rows):
    """Onde há rodovia+km sem coordenada: interpola entre radares da MESMA rodovia que
    têm km E coordenada (marcos de referência). Não usa SNV (não baixado)."""
    refs = defaultdict(list)
    for r in rows:
        if r.get("via") and r.get("km") is not None and r.get("lat") is not None:
            refs[r["via"]].append((r["km"], r["lat"], r["lon"]))
    for v in refs.values():
        v.sort()
    n = 0
    for r in rows:
        if r.get("lat") is None and r.get("via") in refs and r.get("km") is not None:
            p = interpola_km(refs[r["via"]], r["km"])
            if p:
                r["lat"], r["lon"], r["coord_origem"] = p[0], p[1], "interpolado_km"
                n += 1
    return n


def interpola_km(refs, km, max_gap_km=5.0):
    ant = [x for x in refs if x[0] <= km]
    dep = [x for x in refs if x[0] >= km]
    if not ant or not dep:
        return None
    a, b = ant[-1], dep[0]
    if b[0] - a[0] > max_gap_km:
        return None
    if b[0] == a[0]:
        return a[1], a[2]
    # E8: marcos incoerentes (trechos descontínuos com o mesmo número / km não monotônico):
    # a distância real entre os marcos não pode passar muito do intervalo de km.
    dkm = (b[0] - a[0]) * 1000
    if haversine(a[1], a[2], b[1], b[2]) > 1.5 * dkm + 300:
        return None
    t = (km - a[0]) / (b[0] - a[0])
    return a[1] + t * (b[1] - a[1]), a[2] + t * (b[2] - a[2])


def cruzar_inmetro(rows, inmetro_rows, tol_km=0.3):
    """Inmetro traz limite/validade mas não coordenada. Casa por número de série (CET) ou
    rodovia+km (±tol). Retorna nº de radares que receberam limite."""
    por_serie = {str(i["extra"].get("bruto", {}).get("numero_serie") or i["id_fonte"]): i for i in inmetro_rows}
    n = 0
    for r in rows:
        alvo = None
        serie = (r.get("extra") or {}).get("serie") if isinstance(r.get("extra"), dict) else None
        if serie and serie in por_serie:
            alvo = por_serie[serie]
        elif r.get("via") and r.get("km") is not None:
            alvo = next((i for i in inmetro_rows if i.get("via") == r["via"] and i.get("km") is not None
                         and abs(i["km"] - r["km"]) <= tol_km), None)
        if alvo and alvo.get("limite_kmh") and not r.get("limite_kmh"):
            r["limite_kmh"], r["limite_texto"] = alvo["limite_kmh"], alvo["limite_texto"]
            r["limite_origem"] = "inmetro"
            n += 1
    return n


def executar(db_path=None, offline=False, coletores=None):
    con = db.conectar(db_path)
    con.execute("DELETE FROM radares")
    con.execute("DELETE FROM fontes")
    todas, inmetro, espelho, provs = [], [], [], []
    for C in coletores or TODOS:
        radares, prov = C(offline=offline).coletar()
        provs.append(prov)
        if C.nome == "inmetro":
            inmetro = [r.__dict__ | {"id": r.id} for r in radares]
            continue
        if C.nome == "espelho_pardal":
            espelho = [r.__dict__ | {"id": r.id} for r in radares]
            continue
        todas.extend(r.as_row() | {"extra_obj": r.extra} for r in radares)
    for r in todas:
        r["extra"], r["extra_json"] = r.pop("extra_obj"), r["extra"]
        if r.get("limite_kmh") and not r.get("limite_origem"):
            r["limite_origem"] = "oficial"
    n_inm = cruzar_inmetro(todas, inmetro) if inmetro else 0
    n_esp = cruzar_espelho(todas, espelho) if espelho else 0
    n_antt = adicionar_antt_espelho(todas, espelho) if espelho and not any(
        p.fonte == "antt" and p.status_coleta == "ok" for p in provs) else 0
    n_geo = geocodificar_km(todas)
    pares = marcar_duplicatas(todas)
    n_dup_fonte = dedup_mesma_fonte(todas)
    for r in todas:
        r["extra"] = r.pop("extra_json", None) or json.dumps(r["extra"], ensure_ascii=False, default=str)
    db.gravar_radares(con, todas)
    con.executemany("INSERT INTO fontes VALUES(?,?,?,?,?,?,?,?)",
                    [(p.fonte, p.url, p.arquivo, p.sha256, p.data_coleta, p.status_coleta, p.erro, p.registros) for p in provs])
    con.commit()
    validacao = validar(con)
    return {"validacao": validacao, "provs": provs, "total": len(todas), "inmetro_cruzados": n_inm,
            "geocodificados_km": n_geo, "duplicatas": len(pares), "pares": pares,
            "limite_via_espelho": n_esp, "antt_via_espelho": n_antt, "duplicatas_mesma_fonte": n_dup_fonte}


def validar(con, osm_path=None):
    """status_validacao/n_fontes/revisao_osm. Sem cache OSM, roda só com as fontes oficiais."""
    from radar_pi import config
    from radar_pi.validacao import status as val
    p = Path(osm_path or config.OSM_RADARES)
    if p.exists():
        d = json.loads(p.read_text())
        return val.aplicar(con, d["radares"], d.get("origem", p.name))
    return val.aplicar(con, [], "sem_osm")


if __name__ == "__main__":
    import json
    import sys
    res = executar(offline="--offline" in sys.argv)
    print(json.dumps({k: v for k, v in res.items() if k not in ("provs", "pares")}, indent=1))
    for p in res["provs"]:
        print(p.fonte, p.status_coleta, p.registros, p.erro[:120])
