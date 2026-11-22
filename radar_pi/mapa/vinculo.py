"""Vincula cada radar ao trecho de via (aresta dirigida) e ao sentido em que fiscaliza.

E2 (v2): além de CENTRO/BAIRRO e rumos cardeais, interpreta abreviações da CET (C/B, B/C, CB/AS...)
via gazetteer de destinos, o campo RUMO:<graus> do espelho, e geometria (via de mão única).
Radar sem sentido resolvido é vinculado aos dois sentidos SEMPRE com confiança baixa (0,3)."""
from __future__ import annotations

import math

import h3

from . import gazetteer
from .grafo import MalhaOps, norm_nome, to_latlon, to_xy

CENTROS = {"SAO PAULO": (-23.5503, -46.6339), "MOGI DAS CRUZES": (-23.5225, -46.1883)}  # marco zero
RUMO_CARDEAL = {"NORTE": 0, "LESTE": 90, "SUL": 180, "OESTE": 270}
ABREV = {"C/B": "CENTRO/BAIRRO", "B/C": "BAIRRO/CENTRO", "CENTRO-BAIRRO": "CENTRO/BAIRRO",
         "BAIRRO-CENTRO": "BAIRRO/CENTRO", "CENTRO / BAIRRO": "CENTRO/BAIRRO", "BAIRRO / CENTRO": "BAIRRO/CENTRO"}
CONF_BAIXA = 0.3
DIST_MIN_REF = 700.0   # destino a menos disso não dá rumo confiável
MARGEM_GRAUS = 40.0    # um sentido tem que ser claramente melhor que o outro


def _ang(a, b):
    return abs((a - b + 180) % 360 - 180)


def _rumo_xy(ax, ay, bx, by):
    return (math.degrees(math.atan2(bx - ax, by - ay)) + 360) % 360


def _nome_ok(via_radar: str, nome_aresta: str, ref: str | None) -> bool:
    if not via_radar:
        return True
    vr = norm_nome(via_radar)
    if ref and vr.replace(" ", "").replace("-", "") in str(ref).replace(" ", "").replace("-", ""):
        return True
    ta = set(nome_aresta.split()) - {"AV", "R", "AL", "ESTR", "ROD"}
    tr = set(vr.split()) - {"AV", "R", "AL", "ESTR", "ROD"}
    if not ta or not tr:
        return False
    return len(ta & tr) / min(len(ta), len(tr)) >= 0.5


def normalizar_sentido(s: str | None) -> str:
    s = (s or "").upper().strip()
    return ABREV.get(s, ABREV.get(s.replace(" ", ""), s))


def _restricoes(tok, papel, x, y, centro_xy):
    """Rumos desejados (lista) que o token impõe. papel: 'origem' ou 'destino'."""
    t = gazetteer.normalizar(tok)
    if t == "BAIRRO" and centro_xy:
        saindo = _rumo_xy(*centro_xy, x, y)
        return [saindo]  # tanto 'origem centro' quanto 'destino bairro' = afastando do centro
    r = gazetteer.resolver(tok)
    if not r:
        return []
    tipo, val = r
    if tipo == "marginal":
        from shapely.geometry import LineString, Point
        ls = [LineString([to_xy(*p) for p in L]) for L in (gazetteer.MARG_TIETE, gazetteer.MARG_PINH)]
        p = Point(x, y)
        g = min(ls, key=lambda g: g.distance(p))
        q = g.interpolate(g.project(p))
        qx, qy, d = q.x, q.y, g.distance(p)
    elif tipo == "linha":
        from shapely.geometry import LineString, Point
        g = LineString([to_xy(*p) for p in val])
        p = Point(x, y)
        q = g.interpolate(g.project(p))
        qx, qy, d = q.x, q.y, g.distance(p)
    else:
        qx, qy = to_xy(*val)
        d = math.hypot(qx - x, qy - y)
    if d < DIST_MIN_REF:
        return []
    return [_rumo_xy(x, y, qx, qy)] if papel == "destino" else [_rumo_xy(qx, qy, x, y)]


def sentido_alvo(radar: dict, x, y):
    """Devolve (modo, alvo, metodo): modo 'rumo' (alvo=graus) | 'texto' (alvo=lista de rumos desejados) |
    'ambos' | 'indefinido'."""
    s = normalizar_sentido(radar.get("sentido"))
    if s.startswith("RUMO:"):
        try:
            return "rumo", float(s[5:]) % 360, "rumo_espelho"
        except ValueError:
            pass
    if s in RUMO_CARDEAL or s.split(" ")[0] in RUMO_CARDEAL:
        return "rumo", RUMO_CARDEAL[s.split(" ")[0]], "sentido_texto"
    if s in ("AMBOS", "DOISSENTIDOS", "AMBOS OS SENTIDOS"):
        return "ambos", None, "ambos_sentidos"
    centro = CENTROS.get((radar.get("municipio") or "").upper())
    if centro and s in ("CENTRO/BAIRRO", "BAIRRO/CENTRO"):
        cx, cy = to_xy(*centro)
        saindo = _rumo_xy(cx, cy, x, y)
        return "rumo", saindo if s == "CENTRO/BAIRRO" else (saindo + 180) % 360, "sentido_texto"
    if "/" in s:
        o, _, dd = s.partition("/")
        cxy = to_xy(*centro) if centro else None
        if gazetteer.normalizar(o) == "CENTRO" and cxy:
            ro = [_rumo_xy(*cxy, x, y)] if math.hypot(cxy[0] - x, cxy[1] - y) > DIST_MIN_REF else []
        elif gazetteer.normalizar(o) == "BAIRRO" and cxy:
            ro = [(_rumo_xy(*cxy, x, y) + 180) % 360]
        else:
            ro = _restricoes(o, "origem", x, y, cxy)
        if gazetteer.normalizar(dd) == "BAIRRO" and cxy:
            rd = [_rumo_xy(*cxy, x, y)]
        elif gazetteer.normalizar(dd) == "CENTRO" and cxy:
            rd = [(_rumo_xy(*cxy, x, y) + 180) % 360]
        else:
            rd = _restricoes(dd, "destino", x, y, cxy)
        if ro or rd:
            return "texto", ro + rd, "sentido_gazetteer"
    return "indefinido", None, "sentido_indefinido"


def _cel(m, x, y):
    return h3.latlng_to_cell(*to_latlon(x, y), 7)


def vincular(m: MalhaOps, radar: dict, raio=None):
    if radar.get("lat") is None:
        return []
    x, y = to_xy(radar["lat"], radar["lon"])
    raio = raio or (40.0 if radar.get("fonte") in ("cet_sp", "mogi") else 60.0)
    cands = m.candidatos(x, y, raio, max_n=20)
    if not cands:
        return []
    via = radar.get("via") or ""
    modo, alvo, metodo = sentido_alvo(radar, x, y)
    pont = []
    for i, d, off in cands:
        a = m.attr[i]
        ok_nome = _nome_ok(via, a["nome"], a["ref"])
        score = d + (0 if ok_nome else 25)
        if a["highway"] in ("service", "living_street") and radar.get("fonte") != "cet_sp":
            score += 15
        pont.append((score, i, d, off, ok_nome))
    pont.sort()

    def mk(t, met, conf):
        score, i, d, off, ok_nome = t
        u, v, k = m.edges[i]
        return {"radar_id": radar["id"], "grafo": m.nome, "u": u, "v": v, "k": k, "offset_m": off,
                "dist_snap_m": d, "nome_via": m.attr[i]["nome"], "metodo": met + ("" if ok_nome else "+nome_diferente"),
                "confianca": round(conf * (1.0 if ok_nome else 0.6), 3), "rumo": round(m.rumo(i, off), 1),
                "limite_osm": m.attr[i]["maxspeed"], "_i": i}

    best = pont[0]
    r0 = m.rumo(best[1], best[3])
    oposto = next((t for t in pont[1:] if _ang(m.rumo(t[1], t[3]), r0) >= 120), None)

    if modo == "texto":
        dev = lambda r: sum(_ang(r, b) for b in alvo) / len(alvo)
        d0, d1 = dev(r0), dev((r0 + 180) % 360)
        if d0 + MARGEM_GRAUS <= d1:
            modo, alvo = "rumo", r0
        elif d1 + MARGEM_GRAUS <= d0:
            modo, alvo = "rumo", (r0 + 180) % 360
        else:
            modo = "indefinido"
            metodo = "sentido_ambiguo"
    if modo == "rumo":
        comp = [t for t in pont if _ang(m.rumo(t[1], t[3]), alvo) <= 80]
        if comp:
            conf = 0.9 if metodo in ("sentido_texto", "rumo_espelho") else 0.75
            return [mk(comp[0], metodo, conf)]
        return [mk(pont[0], "sentido_conflito", CONF_BAIXA)]
    # E2: inferência geométrica — via de mão única sem pista oposta por perto => sentido único
    if modo == "indefinido" and best[4] and m.attr[best[1]]["oneway"] and oposto is None:
        return [mk(best, "mao_unica", 0.85)]
    conf = 0.8 if modo == "ambos" else CONF_BAIXA
    out = [mk(best, metodo, conf)]
    if oposto:
        out.append(mk(oposto, metodo, conf))
    return out


def vincular_todos(con, m: MalhaOps, filtro_bbox=None):
    rows = [dict(r) for r in con.execute(
        "SELECT * FROM radares WHERE lat IS NOT NULL AND duplicata_de IS NULL")]
    if filtro_bbox:
        s, w, n, e = filtro_bbox
        rows = [r for r in rows if s <= r["lat"] <= n and w <= r["lon"] <= e]
    rows.sort(key=lambda r: h3.latlng_to_cell(r["lat"], r["lon"], 5) + h3.latlng_to_cell(r["lat"], r["lon"], 7))
    con.execute("DELETE FROM vinculos WHERE grafo=?", (m.nome,))
    total, sem = 0, 0
    for r in rows:
        vs = vincular(m, r)
        if not vs:
            sem += 1
        for v in vs:
            v.pop("_i")
            con.execute("INSERT INTO vinculos VALUES(:radar_id,:grafo,:u,:v,:k,:offset_m,:dist_snap_m,:nome_via,"
                        ":metodo,:confianca,:rumo,:limite_osm)", v)
            total += 1
    con.commit()
    return {"radares": len(rows), "vinculos": total, "sem_via_proxima": sem}
