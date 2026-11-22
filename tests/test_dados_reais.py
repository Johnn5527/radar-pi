"""Testes com os dados oficiais reais (CET/DER/ARTESP/DNIT/Mogi) e a malha OSM real de SP."""
import json
import random
import statistics as st

import pytest

from radar_pi.coletor.pipeline import interpola_km, haversine
from radar_pi.sim.executor import rodar
from radar_pi.sim.rotas import caminho_ate_radar, gerar
from conftest import episodios


def _vinc(con, malha, where, params=()):
    q = f"""SELECT r.*, v.u, v.v, v.k, v.offset_m, v.metodo, v.nome_via FROM radares r JOIN vinculos v
            ON v.radar_id=r.id WHERE v.grafo=? AND {where}"""
    out = []
    for row in con.execute(q, (malha.nome, *params)):
        d = dict(row)
        d["_i"] = malha.idx_of.get((d["u"], d["v"], d["k"]))
        if d["_i"] is not None:
            out.append(d)
    return out


def test_cobertura_dados(con_real, registrar):
    c = con_real
    por_fonte = [dict(r) for r in c.execute("""SELECT fonte, count(*) total, sum(status='ativo') ativos,
        sum(status='ativo' AND limite_kmh IS NOT NULL) ativos_com_limite, sum(lat IS NOT NULL) com_coord,
        sum(coord_origem='interpolado_km') coord_interpolada, sum(duplicata_de IS NOT NULL) duplicatas
        FROM radares GROUP BY fonte""")]
    fontes = [dict(r) for r in c.execute("SELECT fonte,status_coleta,registros,erro,data_coleta FROM fontes")]
    pares = [dict(r) for r in c.execute("""SELECT a.fonte de, b.fonte para, count(*) n FROM radares a
        JOIN radares b ON a.duplicata_de=b.id GROUP BY 1,2""")]
    tot = c.execute("SELECT sum(status='ativo' AND duplicata_de IS NULL), sum(status='ativo' AND duplicata_de IS NULL AND limite_kmh IS NOT NULL), sum(lat IS NOT NULL), count(*) FROM radares").fetchone()
    pct_lim = round(100 * tot[1] / tot[0], 1)
    registrar(cenario="cobertura", por_fonte=por_fonte, coletas=fontes, duplicatas=pares,
              ativos_unicos=tot[0], pct_ativos_com_limite=pct_lim, pct_com_coord=round(100 * tot[2] / tot[3], 1))
    falhas = [f["fonte"] for f in fontes if f["status_coleta"] != "ok"]
    assert pct_lim >= 95, f"só {pct_lim}% dos radares ativos têm limite oficial"
    assert not falhas, f"fontes sem coleta nova: {falhas}"


def test_vinculo_cet_sentido(con_real, malha_sp, registrar):
    rows = [dict(r) for r in con_real.execute(
        "SELECT r.id, r.sentido, v.metodo FROM radares r LEFT JOIN vinculos v ON v.radar_id=r.id AND v.grafo='sp' "
        "WHERE r.fonte='cet_sp' AND r.status='ativo'")]
    ids = {r["id"] for r in rows}
    sem = {r["id"] for r in rows if r["metodo"] is None}
    met = {}
    for r in rows:
        met.setdefault(r["id"], r["metodo"])
    cont = {}
    for m in met.values():
        cont[m] = cont.get(m, 0) + 1
    abrev = sorted({r["sentido"] for r in rows if r["sentido"] in ("C/B", "B/C")})
    resolvidos = ("sentido_texto", "sentido_gazetteer", "mao_unica", "rumo_espelho")
    pct_sent = round(100 * sum(1 for m in met.values() if m and m.split("+")[0] in resolvidos) / len(ids), 1)
    registrar(cenario="vinculo_cet", ativos=len(ids), sem_vinculo=len(sem), metodos=cont,
              pct_com_sentido_resolvido=pct_sent, abreviacoes_nao_tratadas=abrev)
    assert len(sem) / len(ids) < 0.02
    assert pct_sent >= 90, f"só {pct_sent}% dos radares CET ativos com sentido resolvido"


def _simular_radar(malha, con, r, seed, vel=50):
    arestas, s_r = caminho_ate_radar(malha, r["_i"], r["offset_m"], 800, 150)
    if s_r < 500:
        return None
    fx, verdade = gerar(malha, arestas, vel_kmh=vel, seed=seed, inicio_m=max(0, s_r - 650), fim_m=s_r + 150)
    q, mm, _ = rodar(malha, con, fx)
    eps = episodios(q)
    rota = set(arestas)
    # v2 (correção do harness): o carro segue pela mesma via depois do fim do trajeto; um radar que está
    # à frente nessa continuação (dentro do horizonte de 700 m) é aviso CORRETO, não falso.
    j, acum = arestas[-1], 0.0
    while j is not None and acum < 700:
        j = malha.continuacao_mesma_via(j)
        if j is None or j in rota:
            break
        rota.add(j)
        acum += malha.attr[j]["len"]
    # radares no MESMO ponto (<=5 m, ex.: 3 registros ARTESP idênticos) contam como o alvo
    mesmos = {x["id"] for x in con.execute("SELECT id, lat, lon FROM radares WHERE status='ativo'")
              if x["lat"] is not None and abs(x["lat"] - r["lat"]) < 5e-5 and abs(x["lon"] - r["lon"]) < 5e-5}
    mesmos.add(r["id"])
    alvo = [e for e in eps if e["id"] in mesmos]
    res = {"id": r["id"], "s_radar": round(s_r), "eps_alvo": alvo, "eps_exato": [e for e in eps if e["id"] == r["id"]], "ms": [x.ms for x in q], "quebras": mm.estado.quebras}
    if alvo:
        k = next(i for i, x in enumerate(q) if x.alerta and x.alerta.radar_id in mesmos)
        res["dist_real_1o"] = round(s_r - verdade[k][0], 1)
        res["erro_dist"] = round(alvo[0]["d_ini"] - (s_r - verdade[k][0]), 1)
        k_pass = next((i for i, v in enumerate(verdade) if v[0] >= s_r + 30), None)
        res["alerta_apos_passar"] = bool(k_pass and any(x.alerta and x.alerta.radar_id in mesmos for x in q[k_pass:]))
    outros = []
    for e in eps:
        if e["id"] in mesmos:
            continue
        ed = con.execute("SELECT u,v,k FROM vinculos WHERE radar_id=? AND grafo=?", (e["id"], malha.nome)).fetchall()
        na_rota = any(malha.idx_of.get(tuple(x)) in rota for x in ed)
        if not na_rota:
            outros.append({"id": e["id"], "incerta": e["incerta"], "d_ini": e["d_ini"]})
    res["falsos"] = outros
    return res


@pytest.fixture(scope="module")
def recall_cet(con_real, malha_sp):
    rads = _vinc(con_real, malha_sp, "r.fonte='cet_sp' AND r.status='ativo'")
    vistos, uniq = set(), []
    for r in rads:
        if r["id"] not in vistos:
            vistos.add(r["id"])
            uniq.append(r)
    random.Random(42).shuffle(uniq)
    out, curtas = [], 0
    for n, r in enumerate(uniq[:150]):
        res = _simular_radar(malha_sp, con_real, r, seed=n)
        if res is None:
            curtas += 1
            continue
        res["metodo"] = r["metodo"]
        out.append(res)
    return out, curtas


def test_cet_real_recall_e_distancia(recall_cet, registrar):
    out, curtas = recall_cet
    det = [r for r in out if r["eps_alvo"]]
    bom = [r for r in det if r["dist_real_1o"] >= 400]
    erros = [abs(r["erro_dist"]) for r in det]
    frag = [r for r in det if len(r["eps_alvo"]) > 1]
    apos = [r for r in det if r["alerta_apos_passar"]]
    registrar(cenario="recall_cet_real", simulados=len(out), rotas_curtas_descartadas=curtas,
              detectados=len(det), pct_detectado=round(100 * len(det) / len(out), 1),
              pct_aviso_com_400m_ou_mais=round(100 * len(bom) / len(out), 1),
              erro_dist_mediana_m=round(st.median(erros), 1) if erros else None,
              erro_dist_p95_m=round(sorted(erros)[int(0.95 * len(erros)) - 1], 1) if erros else None,
              alerta_fragmentado=len(frag), alerta_apos_passar=len(apos),
              nao_detectados=[{"id": r["id"], "metodo": r["metodo"]} for r in out if not r["eps_alvo"]][:15])
    assert len(det) / len(out) >= 0.95, f"detectou {len(det)}/{len(out)}"
    assert len(bom) / len(out) >= 0.90, f"aviso com >=400 m em {len(bom)}/{len(out)}"
    assert not frag, f"{len(frag)} radares com aviso piscando/fragmentado"


def test_cet_real_falsos_alertas(recall_cet, registrar):
    out, _ = recall_cet
    conf = [(r["id"], f) for r in out for f in r["falsos"] if not f["incerta"]]
    inc = [(r["id"], f) for r in out for f in r["falsos"] if f["incerta"]]
    registrar(cenario="falsos_cet_real", rotas=len(out), falsos_confiantes=len(conf), falsos_via_incerta=len(inc),
              rotas_com_falso_confiante=len({a for a, _ in conf}), exemplos=conf[:10])
    assert not conf, f"{len(conf)} avisos confiantes de radar fora da rota em {len({a for a, _ in conf})} rotas"


def test_contramao_real_cet(con_real, malha_sp, registrar):
    """Dirige no sentido OPOSTO ao fiscalizado em vias de mão dupla, para radares CET."""
    rads = _vinc(con_real, malha_sp, "r.fonte='cet_sp' AND r.status='ativo'")
    alvos = []
    for r in rads:
        malha_sp.garantir(*malha_sp.ponto(r["_i"], r["offset_m"]))
        u, v, _ = malha_sp.edges[r["_i"]]
        if malha_sp.R.has_edge(v, u):
            rev = malha_sp.R[v][u]["e"]
            # só se o radar NÃO está vinculado também ao sentido reverso pelos dados
            outros = {x["_i"] for x in rads if x["id"] == r["id"]}
            if rev not in outros:
                alvos.append((r, rev))
    random.Random(7).shuffle(alvos)
    res = []
    for n, (r, rev) in enumerate(alvos[:60]):
        L = malha_sp.attr[rev]["len"]
        sim = _simular_radar(malha_sp, con_real, {**r, "_i": rev, "offset_m": L - r["offset_m"]}, seed=100 + n)
        if sim is None:
            continue
        res.append({"id": r["id"], "metodo": r["metodo"], "avisou": bool(sim["eps_alvo"]),
                    "confiante": any(not e["incerta"] for e in sim["eps_alvo"])})
    errados = [x for x in res if x["confiante"]]
    registrar(cenario="contramao_real", testados=len(res), avisou=sum(x["avisou"] for x in res),
              avisou_confiante=len(errados), exemplos=errados[:8])
    assert not errados, f"{len(errados)}/{len(res)} avisos confiantes na contramão"


def test_marginal_real_expressa_vs_local(con_real, malha_sp, registrar):
    """Radar 90/60 da pista expressa (Marginais). Dirige pela pista paralela (local/central) e pela expressa."""
    rads = _vinc(con_real, malha_sp, "r.status='ativo' AND r.limite_texto='90/60'")
    casos = []
    for r in rads:
        malha_sp.garantir(*malha_sp.ponto(r["_i"], r["offset_m"]))
        rr = malha_sp.rumo(r["_i"], r["offset_m"])
        x, y = malha_sp.ponto(r["_i"], r["offset_m"])
        for i, d, off in malha_sp.candidatos(x, y, 45):
            a = malha_sp.attr[i]
            if i != r["_i"] and "MARG" in a["nome"] and "EXPRESSA" not in a["nome"] and \
               abs((malha_sp.rumo(i, off) - rr + 180) % 360 - 180) < 30:
                casos.append((r, i, off, round(d, 1)))
                break
    res = []
    for n, (r, i_loc, off_loc, d) in enumerate(casos[:20]):
        exp = _simular_radar(malha_sp, con_real, r, seed=200 + n, vel=85)
        loc = _simular_radar(malha_sp, con_real, {**r, "_i": i_loc, "offset_m": off_loc}, seed=300 + n, vel=60)
        if not exp or not loc:
            continue
        res.append({"id": r["id"], "dist_pistas_m": d, "via_local": malha_sp.attr[i_loc]["nome_orig"],
                    "expressa_avisou": bool(exp["eps_alvo"]),
                    "expressa_txt_90_60": bool(exp["eps_alvo"]),
                    "local_avisou_confiante": any(not e["incerta"] for e in loc["eps_alvo"]),
                    "local_avisou_incerta": any(e["incerta"] for e in loc["eps_alvo"])})
    registrar(cenario="marginal_real", pares=len(casos), testados=len(res), detalhes=res)
    assert res, "nenhum par expressa/local encontrado"
    assert all(x["expressa_avisou"] for x in res), "faltou aviso na expressa"
    assert not any(x["local_avisou_confiante"] for x in res), "avisou com certeza o radar da expressa estando na local"


def test_desativado_real(con_real, malha_sp, registrar):
    rads = _vinc(con_real, malha_sp, "r.fonte='cet_sp' AND r.status='inativo'")
    random.Random(3).shuffle(rads)
    res = []
    for n, r in enumerate(rads[:30]):
        sim = _simular_radar(malha_sp, con_real, r, seed=400 + n)
        if sim:
            res.append({"id": r["id"], "avisou": bool(sim["eps_exato"]), "outro_ativo_mesmo_ponto_avisou": bool(sim["eps_alvo"]) and not sim["eps_exato"]})
    registrar(cenario="desativado_real", testados=len(res), avisou=sum(x["avisou"] for x in res))
    assert res and not any(x["avisou"] for x in res)


def test_tamoios_sp099(con_real, malha_tamoios, registrar):
    rads = _vinc(con_real, malha_tamoios, "r.status='ativo' AND r.via='SP-099'")
    res = []
    for n, r in enumerate(rads[:20]):
        sim = _simular_radar(malha_tamoios, con_real, r, seed=500 + n, vel=90)
        if sim is None:
            res.append({"id": r["id"], "rota_curta": True})
            continue
        res.append({"id": r["id"], "tipo": r["tipo"], "sentido": r["sentido"], "metodo": r["metodo"],
                    "via_osm": r["nome_via"], "avisou": bool(sim["eps_alvo"]),
                    "dist_1o": sim.get("dist_real_1o"), "falsos": sim["falsos"]})
    ok = [x for x in res if x.get("avisou")]
    registrar(cenario="tamoios", radares_sp099_ativos_vinculados=len({r["id"] for r in rads}), testados=len(res),
              avisou=len(ok), detalhes=res)
    assert res and len(ok) == len([x for x in res if not x.get("rota_curta")])


def test_geocodificacao_rodovia_km_leave_one_out(con_real, registrar):
    """Valida a interpolação rodovia+km (usada para Inmetro): remove um radar DER-SP e
    estima sua posição pelos vizinhos da mesma rodovia."""
    rows = [dict(r) for r in con_real.execute(
        "SELECT via, km, lat, lon FROM radares WHERE fonte IN ('der_sp') AND km IS NOT NULL AND lat IS NOT NULL")]
    por = {}
    for r in rows:
        por.setdefault(r["via"], []).append((r["km"], r["lat"], r["lon"]))
    erros, sem = [], 0
    for via, lst in por.items():
        lst.sort()
        for j, (km, lat, lon) in enumerate(lst):
            outros = lst[:j] + lst[j + 1:]
            p = interpola_km(outros, km)
            if p is None:
                sem += 1
                continue
            erros.append(haversine(lat, lon, *p))
    erros.sort()
    med = round(st.median(erros), 1)
    registrar(cenario="geocod_km", testados=len(erros), sem_referencia=sem, mediana_m=med,
              p90_m=round(erros[int(0.9 * len(erros))], 1), max_m=round(erros[-1], 1),
              pct_ate_100m=round(100 * sum(e <= 100 for e in erros) / len(erros), 1))
    assert med <= 100, f"erro mediano {med} m"
