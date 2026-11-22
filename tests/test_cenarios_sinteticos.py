"""Cenários controlados em malhas sintéticas (coordenadas reais perto do centro de SP).
Leste = afastando do marco zero (Sé fica ~370 m a oeste da base) => sentido CENTRO/BAIRRO."""
import math

from radar_pi.mapa.grafo import norm_nome, to_xy
from radar_pi.sim.executor import rodar
from radar_pi.sim.rotas import caminho_por_nos, gerar
from radar_pi.sim.sintetico import Construtor, ponto_ll
from radar_pi.voz.tts import Voz
from conftest import episodios, montar, radar


def _rua_a_b(dist_b=60.0, mao_b=True, hw_a="primary", hw_b="primary", nome_b="Rua B"):
    c = Construtor()
    a = c.via([(0, 0), (1600, 0)], "Rua A", highway=hw_a, seg_m=100)
    b = c.via([(0, dist_b), (1600, dist_b)], nome_b, mao_dupla=mao_b, highway=hw_b, seg_m=100)
    return c, a, b


def test_rua_paralela_avisa_so_a(registrar):
    c, a, b = _rua_a_b(60)
    m, con, info = montar(c, [radar("A", 900, 0, 50, via="RUA A"), radar("B", 600, 60, 40, via="RUA B")])
    fixes, _ = gerar(m, caminho_por_nos(m, a), vel_kmh=50, inicio_m=300, fim_m=1200, seed=3)
    q, mm, _ = rodar(m, con, fixes)
    eps = episodios(q)
    ids = {e["id"] for e in eps}
    registrar(cenario="rua_paralela", episodios=eps, vinculos=info)
    assert "cet_sp:B" not in ids, f"avisou radar da rua B: {eps}"
    ea = [e for e in eps if e["id"] == "cet_sp:A"]
    assert ea and 420 <= ea[0]["d_ini"] <= 460, f"radar A: {ea}"


def test_contramao_nao_avisa(registrar):
    c, a, _ = _rua_a_b(200)
    m, con, _ = montar(c, [radar("A", 900, 0, 50, via="RUA A", sentido="CENTRO/BAIRRO")])
    oeste, _ = gerar(m, caminho_por_nos(m, list(reversed(a))), vel_kmh=50, inicio_m=200, fim_m=1400, seed=4)
    leste, _ = gerar(m, caminho_por_nos(m, a), vel_kmh=50, inicio_m=300, fim_m=1200, seed=5)
    e_oeste = episodios(rodar(m, con, oeste)[0])
    e_leste = episodios(rodar(m, con, leste)[0])
    registrar(cenario="contramao", sentido_oposto=e_oeste, mesmo_sentido=e_leste)
    assert not e_oeste, f"avisou no sentido oposto: {e_oeste}"
    assert e_leste, "não avisou no sentido certo (controle)"


def test_marginal_colada_15m(registrar):
    """Expressa e local, mesma direção, 15 m entre eixos, sem ligação no trecho.
    Esperado: na expressa NÃO dar aviso 'confiante' do radar da local (no máximo 'via incerta')
    e SEMPRE avisar o radar da expressa."""
    c = Construtor()
    exp = c.via([(0, 0), (1600, 0)], "Marginal Expressa", mao_dupla=False, highway="motorway", seg_m=100)
    c.via([(0, 15), (1600, 15)], "Marginal Local", mao_dupla=False, highway="primary", seg_m=100)
    m, con, _ = montar(c, [radar("LOC", 900, 15, 50, via="MARGINAL LOCAL"),
                           radar("EXP", 1300, 0, 90, via="MARGINAL EXPRESSA", texto="90/60")])
    res = []
    for seed in range(1, 11):
        fx, _ = gerar(m, caminho_por_nos(m, exp), vel_kmh=80, inicio_m=200, fim_m=1500, seed=seed)
        q, mm, _ = rodar(m, con, fx)
        eps = episodios(q)
        on_exp = sum(1 for qq in q if qq.i is not None and m.attr[qq.i]["nome"] == norm_nome("Marginal Expressa")) / len(q)
        res.append({"seed": seed, "episodios": eps, "pct_quadros_na_expressa": round(on_exp, 3)})
    confiante_errado = [r for r in res if any(e["id"] == "cet_sp:LOC" and not e["incerta"] for e in r["episodios"])]
    perdeu_exp = [r for r in res if not any(e["id"] == "cet_sp:EXP" for e in r["episodios"])]
    registrar(cenario="marginal_colada", rodadas=res, confiante_errado=len(confiante_errado),
              perdeu_radar_expressa=len(perdeu_exp))
    assert not confiante_errado, f"{len(confiante_errado)}/10 rodadas avisaram radar da LOCAL como certo"
    assert not perdeu_exp, f"{len(perdeu_exp)}/10 rodadas não avisaram o radar da EXPRESSA"


def test_curva_distancia_ao_longo_da_via(registrar):
    c = Construtor()
    nos, pts = c.via_curva((0, 0), 300, -90, 90, "Av Curva", n=36)  # semicírculo anti-horário pelo lado leste
    arco = math.pi * 300
    s_radar = arco - 60
    ang_r = -90 + 180 * s_radar / arco
    xr, yr = 300 * math.cos(math.radians(ang_r)), 300 * math.sin(math.radians(ang_r))
    m, con, _ = montar(c, [radar("C", xr, yr, 50, via="AV CURVA", sentido="OESTE")])
    fx, verdade = gerar(m, caminho_por_nos(m, nos), vel_kmh=40, seed=7, ruido=(5, 8))
    q, _, _ = rodar(m, con, fx)
    eps = episodios(q)
    if not eps:
        registrar(cenario="curva", ep=eps)
        assert eps, "não avisou na curva"
    k = next(i for i, qq in enumerate(q) if qq.alerta)
    rx, ry = to_xy(*ponto_ll(xr, yr))
    p = verdade[k]
    reta = math.hypot(p[1] - rx, p[2] - ry)
    registrar(cenario="curva", ep=eps, dist_via_real=round(s_radar - p[0], 1), dist_reta=round(reta, 1),
              dist_exibida=eps[0]["d_ini"])
    assert abs((s_radar - p[0]) - 450) <= 30, f"começou a {s_radar - p[0]:.0f} m pela via"
    assert reta < 440, "na curva a reta deveria ser menor que 450 no início do aviso"


def test_troca_de_rua(registrar):
    c = Construtor()
    a = c.via([(0, 0), (1600, 0)], "Rua A", seg_m=100)
    d = c.via([(200, -600), (200, 0)], "Rua D", seg_m=100)
    c.via([(200, 0), (200, 400)], "Rua D", seg_m=100)
    m, con, _ = montar(c, [radar("A", 650, 0, 50, via="RUA A")])
    nos = d + a[3:]  # desce a Rua D para o norte, vira à direita (leste) na Rua A em x=200
    fx, verdade = gerar(m, caminho_por_nos(m, nos), vel_kmh=40, seed=8, fim_m=1300)
    q, _, _ = rodar(m, con, fx)
    eps = episodios(q)
    k_virada = next(i for i, v in enumerate(verdade) if v[0] >= 600)
    registrar(cenario="troca_de_rua", ep=eps, t_virada=fx[k_virada].t)
    assert eps, "não avisou após virar na Rua A"
    atraso = eps[0]["t_ini"] - fx[k_virada].t
    assert atraso <= 5, f"aviso {atraso:.0f}s após a virada"
    assert eps[0]["d_ini"] <= 455


def test_passou_do_radar_para_alerta(registrar):
    c, a, _ = _rua_a_b(300)
    m, con, _ = montar(c, [radar("A", 900, 0, 50, via="RUA A")])
    fx, verdade = gerar(m, caminho_por_nos(m, a), vel_kmh=50, inicio_m=300, fim_m=1550, seed=9)
    q, _, _ = rodar(m, con, fx)
    eps = episodios(q)
    k_pass = next(i for i, v in enumerate(verdade) if v[0] >= 900)
    depois = [qq for qq in q[k_pass + 3:] if qq.alerta]
    registrar(cenario="passou_radar", ep=eps, quadros_com_alerta_3s_apos_passar=len(depois))
    assert len(eps) == 1, f"alerta reapareceu/fragmentou: {eps}"
    assert not depois, f"{len(depois)} quadros ainda alertando 3 s após passar"


def test_gps_ruidoso_15m(registrar):
    c, a, b = _rua_a_b(60)
    m, con, _ = montar(c, [radar("A", 900, 0, 50, via="RUA A"), radar("B", 700, 60, 40, via="RUA B")])
    falhas = []
    for seed in range(20, 40):
        fx, _ = gerar(m, caminho_por_nos(m, a), vel_kmh=50, inicio_m=300, fim_m=1200, seed=seed, ruido=(12, 15))
        eps = episodios(rodar(m, con, fx)[0])
        ok_a = [e for e in eps if e["id"] == "cet_sp:A"]
        if any(e["id"] == "cet_sp:B" for e in eps) or len(ok_a) != 1 or not (400 <= ok_a[0]["d_ini"] <= 470):
            falhas.append({"seed": seed, "eps": eps})
    registrar(cenario="gps_ruidoso", rodadas=20, falhas=len(falhas), exemplos=falhas[:3])
    assert not falhas, f"{len(falhas)}/20 rodadas com problema"


def test_tunel_perda_de_sinal(registrar):
    c, a, _ = _rua_a_b(300)
    m, con, _ = montar(c, [radar("T", 1000, 0, 50, via="RUA A")])
    # túnel de 300 a 800 m; radar em 1000 => aviso deveria começar em 550 (dentro do túnel)
    fx, verdade = gerar(m, caminho_por_nos(m, a), vel_kmh=50, inicio_m=100, fim_m=1300, seed=11, perda=[(300, 800)])
    q, _, _ = rodar(m, con, fx)
    eps = episodios(q)
    k = next((i for i, qq in enumerate(q) if qq.alerta), None)
    real = 1000 - verdade[k][0] if k is not None else None
    amostras = [(round(1000 - verdade[i][0]), round(qq.alerta.distancia_m) if qq.alerta else None)
                for i, qq in enumerate(q) if qq.sem_gps][::5]
    registrar(cenario="tunel", ep=eps, dist_real_no_1o_aviso=real, amostras_tunel_real_vs_exibido=amostras)
    assert k is not None, "nunca avisou"
    assert real >= 300, f"primeiro aviso só a {real:.0f} m do radar"


def test_velocidade_acima_abaixo(registrar):
    c, a, _ = _rua_a_b(300)
    m, con, _ = montar(c, [radar("V", 900, 0, 50, via="RUA A")])
    out = {}
    for v in (40, 65):
        fx, _ = gerar(m, caminho_por_nos(m, a), vel_kmh=v, inicio_m=300, fim_m=1000, seed=12)
        al = [qq.alerta for qq in rodar(m, con, fx)[0] if qq.alerta]
        out[v] = (sum(x.acima for x in al), len(al))
    registrar(cenario="velocidade", resultado=out)
    assert out[40][0] == 0 and out[40][1] > 0
    assert out[65][0] == out[65][1] > 0


def test_limite_composto_90_60(registrar, tmp_path):
    c, a, _ = _rua_a_b(300)
    m, con, _ = montar(c, [radar("M", 900, 0, 90, via="RUA A", texto="90/60")])
    fx, _ = gerar(m, caminho_por_nos(m, a), vel_kmh=80, inicio_m=300, fim_m=1000, seed=13)
    voz = Voz(pasta=tmp_path)
    al = [qq.alerta for qq in rodar(m, con, fx, voz=voz)[0] if qq.alerta]
    registrar(cenario="limite_composto", texto=al[0].limite_texto if al else None, acima=any(x.acima for x in al),
              voz=voz.log)
    assert al and al[0].limite_texto == "90" and al[0].limite_composto == "90/60"
    assert not any(x.acima for x in al), "80 km/h não deveria ficar vermelho (90 p/ leves)"
    assert voz.log and voz.log[0]["texto"] == "radar, 90" and voz.log[0]["wav"]


def test_limite_composto_pesado_e11(registrar, monkeypatch):
    """E11: com RADAR_VEICULO=pesado, '90/60' vale 60 (80 km/h fica vermelho)."""
    from radar_pi import config
    monkeypatch.setattr(config, "VEICULO", "pesado")
    c, a, _ = _rua_a_b(300)
    m, con, _ = montar(c, [radar("M", 900, 0, 90, via="RUA A", texto="90/60")])
    fx, _ = gerar(m, caminho_por_nos(m, a), vel_kmh=80, inicio_m=300, fim_m=1000, seed=13)
    al = [qq.alerta for qq in rodar(m, con, fx)[0] if qq.alerta]
    registrar(cenario="limite_composto_pesado", texto=al[0].limite_texto if al else None, acima=all(x.acima for x in al))
    assert al and al[0].limite_texto == "60" and all(x.acima for x in al)


def test_radar_desativado_nao_avisa(registrar):
    c, a, _ = _rua_a_b(300)
    m, con, _ = montar(c, [radar("D", 900, 0, 50, via="RUA A", status="inativo")])
    fx, _ = gerar(m, caminho_por_nos(m, a), vel_kmh=50, inicio_m=300, fim_m=1100, seed=14)
    eps = episodios(rodar(m, con, fx)[0])
    registrar(cenario="desativado", ep=eps)
    assert not eps


def test_radar_sem_limite_exibicao(registrar, tmp_path):
    c = Construtor()
    a = c.via([(0, 0), (1600, 0)], "SP-099", highway="trunk", maxspeed=80, seg_m=100)
    b = c.via([(0, 300), (1600, 300)], "SP-098", highway="trunk", seg_m=100)
    m, con, _ = montar(c, [radar("S1", 900, 0, None, via="SP-099", sentido="LESTE", fonte="der_sp"),
                           radar("S2", 900, 300, None, via="SP-098", sentido="LESTE", fonte="der_sp")])
    voz = Voz(pasta=tmp_path)
    out = {}
    for nome, nos in (("com_maxspeed_osm", a), ("sem_maxspeed_osm", b)):
        fx, _ = gerar(m, caminho_por_nos(m, nos), vel_kmh=90, inicio_m=300, fim_m=1000, seed=15)
        al = [qq.alerta for qq in rodar(m, con, fx, voz=voz)[0] if qq.alerta]
        out[nome] = {"texto": al[0].limite_texto if al else None, "acima": al[0].acima if al else None}
    registrar(cenario="sem_limite", resultado=out, voz=[v["texto"] for v in voz.log])
    assert out["com_maxspeed_osm"]["texto"] == "80*" and out["com_maxspeed_osm"]["acima"]
    assert out["sem_maxspeed_osm"]["texto"] == "?"
    assert [v["texto"] for v in voz.log] == ["radar, 80", "radar adiante"]
