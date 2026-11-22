"""Métricas de E2 (sentido) e E1 (limite) para o relatório."""
import json
import math
import sqlite3
import sys
from collections import Counter

db = sys.argv[1] if len(sys.argv) > 1 else "data/radares.sqlite"
grafo = sys.argv[2] if len(sys.argv) > 2 else "sp"
con = sqlite3.connect(db)
con.row_factory = sqlite3.Row
out = {}
ativos = [dict(r) for r in con.execute("SELECT * FROM radares WHERE status='ativo' AND duplicata_de IS NULL")]
out["ativos"] = len(ativos)
cols = [r[1] for r in con.execute("PRAGMA table_info(radares)")]
out["ativos_com_limite"] = sum(1 for r in ativos if r["limite_kmh"])
if "limite_origem" in cols:
    out["limite_origem"] = dict(Counter(r["limite_origem"] for r in ativos if r["limite_kmh"]))
vcols = [r[1] for r in con.execute("PRAGMA table_info(vinculos)")]
V = {}
for v in con.execute("SELECT * FROM vinculos WHERE grafo=?", (grafo,)):
    V.setdefault(v["radar_id"], []).append(dict(v))
cet = [r for r in ativos if r["fonte"] == "cet_sp"]
cet_v = [r for r in cet if r["id"] in V]
res = [r for r in cet_v if len(V[r["id"]]) == 1 and V[r["id"]][0]["confianca"] >= 0.5]
out["cet_ativos"] = len(cet)
out["cet_vinculados"] = len(cet_v)
out["cet_sentido_resolvido"] = len(res)
out["cet_sentido_resolvido_pct_vinculados"] = round(100 * len(res) / max(1, len(cet_v)), 1)
out["metodos_cet"] = dict(Counter(V[r["id"]][0]["metodo"].split("+")[0] for r in cet_v))
amb = [r for r in ativos if r["id"] in V and len(V[r["id"]]) > 1]
out["vinculados_2_sentidos"] = len(amb)
out["vinculados_2_sentidos_conf_alta"] = sum(1 for r in amb if V[r["id"]][0]["confianca"] >= 0.5
                                            and not V[r["id"]][0]["metodo"].startswith("ambos_sentidos"))
todos_v = [r for r in ativos if r["id"] in V]
if "limite_osm" in vcols:
    sem = [r for r in todos_v if not r["limite_kmh"]]
    out["sem_limite_oficial_mas_com_osm"] = sum(1 for r in sem if V[r["id"]][0].get("limite_osm"))
    out["limite_efetivo_pct_vinculados"] = round(100 * sum(1 for r in todos_v if r["limite_kmh"] or V[r["id"]][0].get("limite_osm")) / max(1, len(todos_v)), 1)
    out["limite_oficial_pct_vinculados"] = round(100 * sum(1 for r in todos_v if r["limite_kmh"]) / max(1, len(todos_v)), 1)
# consistência de pares espelhados (X/Y e Y/X a <60 m): rumos devem ser opostos
if "rumo" in vcols:
    def xy(r):
        return r["lat"] * 111320, r["lon"] * 111320 * math.cos(math.radians(r["lat"]))
    pares, ok = 0, 0
    sres = [r for r in res if r["sentido"] and "/" in r["sentido"] and r["sentido"] not in ("C/B", "B/C", "CENTRO/BAIRRO", "BAIRRO/CENTRO")]
    for i, a in enumerate(sres):
        o, _, d = a["sentido"].partition("/")
        for b in sres[i + 1:]:
            if b["sentido"] == f"{d}/{o}" and math.dist(xy(a), xy(b)) < 150:
                pares += 1
                ra, rb = V[a["id"]][0]["rumo"], V[b["id"]][0]["rumo"]
                ok += abs((ra - rb + 180) % 360 - 180) >= 120
    out["pares_espelhados_gazetteer"] = pares
    out["pares_espelhados_consistentes"] = ok
print(json.dumps(out, ensure_ascii=False, indent=1))
