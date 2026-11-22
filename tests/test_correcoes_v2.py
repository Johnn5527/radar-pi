"""Testes novos, um por correção (E1–E11, N1–N10). Cada um registra métricas em resultados/metricas.jsonl."""
import os
import time

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import pytest

from radar_pi.mapa.grafo import to_xy
from radar_pi.sim.executor import rodar
from radar_pi.sim.rotas import caminho_por_nos, gerar
from radar_pi.sim.sintetico import Construtor, ponto_ll
from conftest import ROOT, episodios, montar, radar


def _via_leste_oeste(mao_dupla=True, nome="Av Teste"):
    c = Construtor()
    nos = c.via([(0, 0), (1600, 0)], nome, mao_dupla=mao_dupla, highway="primary", seg_m=100)
    return c, nos


# --- E2: sentido ----------------------------------------------------------------------------------
def test_e2_gazetteer_destinos(registrar):
    """'CONSOLACAO/PENHA' perto da Sé: de oeste para leste => vínculo só no sentido leste (rumo ~90)."""
    c, nos = _via_leste_oeste()
    m, con, _ = montar(c, [radar("L", 900, 0, 50, sentido="CONSOLACAO/PENHA", via="AV TESTE"),
                           radar("O", 900, 0, 50, sentido="PENHA/CONSOLACAO", via="AV TESTE"),
                           radar("AB", 700, 0, 50, sentido="CB/AS", via="AV TESTE")])
    v = {r["radar_id"]: [dict(x) for x in con.execute("SELECT * FROM vinculos WHERE radar_id=?", (r["radar_id"],))]
         for r in con.execute("SELECT DISTINCT radar_id FROM vinculos")}
    registrar(cenario="e2_gazetteer", vinculos={k: [(x["metodo"], x["rumo"], x["confianca"]) for x in l] for k, l in v.items()})
    assert len(v["cet_sp:L"]) == 1 and abs(v["cet_sp:L"][0]["rumo"] - 90) < 20 and v["cet_sp:L"][0]["metodo"] == "sentido_gazetteer"
    assert len(v["cet_sp:O"]) == 1 and abs(v["cet_sp:O"][0]["rumo"] - 270) < 20
    # Castelo Branco (oeste) -> Ayrton Senna (leste)
    assert len(v["cet_sp:AB"]) == 1 and abs(v["cet_sp:AB"][0]["rumo"] - 90) < 20


def test_e2_abreviacoes_e_rumo_espelho():
    from radar_pi.mapa.vinculo import normalizar_sentido, sentido_alvo
    assert normalizar_sentido("C/B") == "CENTRO/BAIRRO" and normalizar_sentido("B/C") == "BAIRRO/CENTRO"
    x, y = to_xy(-23.55, -46.60)
    assert sentido_alvo({"sentido": "RUMO:184"}, x, y)[:2] == ("rumo", 184.0)
    assert sentido_alvo({"sentido": "C/B", "municipio": "SAO PAULO"}, x, y)[0] == "rumo"


def test_e2_indefinido_baixa_confianca_e_placa(registrar):
    """Sem sentido: vínculo nos 2 sentidos SEMPRE com confiança baixa; aviso sai 'via incerta' e não vira placa (N6)."""
    from radar_pi.mapa_ui.navegador import Navegador
    c, nos = _via_leste_oeste()
    m, con, _ = montar(c, [radar("X", 900, 0, 50, sentido=None, via="AV TESTE")])
    vs = [dict(x) for x in con.execute("SELECT * FROM vinculos")]
    fx, _ = gerar(m, caminho_por_nos(m, nos), vel_kmh=50, inicio_m=300, fim_m=1000, seed=3)
    q, _, _ = rodar(m, con, fx)
    al = [x.alerta for x in q if x.alerta]
    nav = Navegador(m, con)
    placas = []
    for f in fx:
        nav.novo_fix(f, f.t)
        placas.append(nav.placa)
    registrar(cenario="e2_indefinido", vinculos=[(x["metodo"], x["confianca"]) for x in vs],
              avisos=len(al), incertos=sum(a.via_incerta for a in al), placas=sum(p is not None for p in placas))
    assert len(vs) == 2 and all(x["confianca"] <= 0.3 for x in vs)
    assert al and all(a.via_incerta for a in al)
    assert not any(placas)


def test_e2_mao_unica_resolve_sentido():
    c, nos = _via_leste_oeste(mao_dupla=False)
    m, con, _ = montar(c, [radar("U", 900, 0, 50, sentido=None, via="AV TESTE")])
    vs = [dict(x) for x in con.execute("SELECT * FROM vinculos")]
    assert len(vs) == 1 and vs[0]["metodo"] == "mao_unica" and vs[0]["confianca"] >= 0.8


# --- E3: túnel ----------------------------------------------------------------------------------
def test_e3_dead_reckoning_distancia_no_tunel(registrar):
    c = Construtor()
    a = c.via([(0, 0), (1600, 0)], "Rua A", seg_m=100)
    m, con, _ = montar(c, [radar("T", 1000, 0, 50, via="RUA A")])
    fx, ver = gerar(m, caminho_por_nos(m, a), vel_kmh=50, inicio_m=100, fim_m=1100, seed=11, perda=[(300, 800)])
    q, _, _ = rodar(m, con, fx)
    erros = [abs((1000 - ver[i][0]) - qq.alerta.distancia_m) for i, qq in enumerate(q) if qq.sem_gps and qq.alerta]
    n_sem = sum(1 for qq in q if qq.sem_gps)
    registrar(cenario="e3_tunel", quadros_sem_gps=n_sem, com_aviso=len(erros), erro_max_m=round(max(erros), 1) if erros else None)
    assert erros and max(erros) < 60


# --- E4/E5 -------------------------------------------------------------------------------------
def test_e5_histerese_parado_no_limiar(registrar):
    """Carro quase parado a ~450 m do radar com GPS ruidoso (12–15 m): o aviso não pode piscar."""
    c = Construtor()
    a = c.via([(0, 0), (1600, 0)], "Rua A", seg_m=100)
    m, con, _ = montar(c, [radar("H", 1000, 0, 50, via="RUA A")])
    falhas = []
    for seed in range(5):
        fx, _ = gerar(m, caminho_por_nos(m, a), vel_func=lambda s: 40 if s < 540 else 1.0, inicio_m=300, fim_m=575,
                      seed=seed, ruido=(12, 15))
        eps = [e for e in episodios(rodar(m, con, fx)[0]) if e["id"] == "cet_sp:H"]
        if len(eps) > 1:
            falhas.append(len(eps))
    registrar(cenario="e5_histerese", rodadas=5, rodadas_piscando=len(falhas), episodios=falhas)
    assert not falhas


def test_e5_voz_uma_vez_por_radar(tmp_path):
    from radar_pi.voz.tts import Voz
    c = Construtor()
    a = c.via([(0, 0), (1600, 0)], "Rua A", seg_m=100)
    m, con, _ = montar(c, [radar("V", 1000, 0, 50, via="RUA A")])
    fx, _ = gerar(m, caminho_por_nos(m, a), vel_kmh=50, inicio_m=300, fim_m=1100, seed=2, ruido=(12, 15))
    voz = Voz(pasta=tmp_path)
    rodar(m, con, fx, voz=voz)
    assert [v["texto"] for v in voz.log] == ["radar, 50"]


def test_e6_via_muda_de_nome_em_linha_reta(registrar):
    """Radar logo depois de a via trocar de nome (mesma reta): aviso começa ainda na via anterior."""
    c = Construtor()
    a = c.via([(0, 0), (800, 0)], "Rua A", seg_m=100)
    b = c.via([(800, 0), (1600, 0)], "Rua C", seg_m=100)
    c.via([(800, -400), (800, 400)], "Rua Cruzando", highway="residential", seg_m=100)
    m, con, _ = montar(c, [radar("C", 1000, 0, 50, via="RUA C")])
    fx, ver = gerar(m, caminho_por_nos(m, a + b[1:]), vel_kmh=50, inicio_m=300, fim_m=1100, seed=4)
    q, _, _ = rodar(m, con, fx)
    k = next((i for i, x in enumerate(q) if x.alerta), None)
    real = 1000 - ver[k][0] if k is not None else None
    registrar(cenario="e6_troca_nome", dist_real_1o=real)
    assert real is not None and real >= 400


# --- coleta/dados --------------------------------------------------------------------------------
def test_e8_interpolacao_rejeita_marcos_incoerentes():
    from radar_pi.coletor.pipeline import interpola_km
    refs = [(10.0, -23.50, -46.60), (12.0, -23.51, -46.61), (13.0, -22.00, -47.90)]   # km 13 a 150 km (outro trecho)
    assert interpola_km(refs, 11.0) is not None
    assert interpola_km(refs, 12.5) is None
    assert interpola_km(refs, 40.0) is None   # longe demais de qualquer marco


def test_e10_dedup_mesma_fonte():
    from radar_pi.coletor.pipeline import dedup_mesma_fonte
    rows = [{"id": f"artesp:{i}", "fonte": "artesp", "lat": -23.4, "lon": -45.6 + i * 1e-6, "status": "ativo"} for i in range(3)]
    rows.append({"id": "artesp:x", "fonte": "artesp", "lat": -23.41, "lon": -45.6, "status": "ativo"})
    assert dedup_mesma_fonte(rows) == 2 and sum(1 for r in rows if not r.get("duplicata_de")) == 2


def test_e7_coletor_tenta_de_novo(tmp_path, monkeypatch):
    from radar_pi.coletor import base
    from test_unidades import _Fake
    c = _Fake(raw_dir=tmp_path)
    c.url = "http://exemplo.invalido/r.csv"
    chamadas = []

    def falha_depois_ok(url):
        chamadas.append(url)
        if len(chamadas) < 3:
            raise IOError("timeout")
        return b"id,lat,lon\n" + b"1,-23.5,-46.6\n" * 30

    monkeypatch.setattr(c, "_baixar_uma", falha_depois_ok)
    monkeypatch.setattr(c, "_baixar_curl", falha_depois_ok)
    monkeypatch.setattr(base.time, "sleep", lambda s: None)
    c.tentativas = 3
    assert c._baixar().startswith(b"id,lat") and len(chamadas) == 3


def test_e1_cobertura_limite_por_origem(con_real, registrar):
    tot = con_real.execute("SELECT count(*) FROM radares WHERE status='ativo' AND duplicata_de IS NULL").fetchone()[0]
    por = {r[0]: r[1] for r in con_real.execute(
        "SELECT coalesce(limite_origem,'sem_limite'), count(*) FROM radares WHERE status='ativo' AND duplicata_de IS NULL "
        "AND (limite_kmh IS NOT NULL OR 1) GROUP BY 1")}
    com = sum(v for k, v in por.items() if k != "sem_limite")
    registrar(cenario="e1_limite_origem", ativos=tot, por_origem=por, pct_com_limite=round(100 * com / tot, 1),
              pct_limite_fonte_direta=round(100 * por.get("oficial", 0) / tot, 1))
    assert com / tot > 0.8


# --- UI --------------------------------------------------------------------------------------------
def test_n2_voz_nao_bloqueia(tmp_path, registrar):
    from radar_pi.voz.tts import Voz
    v = Voz(pasta=tmp_path, assincrono=True)
    t0 = time.perf_counter()
    reg = v.falar("radar, 50")
    ms = (time.perf_counter() - t0) * 1000
    v.esperar(10)
    registrar(cenario="n2_voz_async", ms_chamada=round(ms, 3), ms_espeak=reg.get("ms"), wav=bool(reg.get("wav")))
    v.fechar()
    assert ms < 5
    if v.disponivel:
        assert reg["wav"] and os.path.exists(reg["wav"])


def _renderer(w, h):
    from radar_pi.mapa_ui.base import MapaBase
    from radar_pi.mapa_ui.render import Renderer
    from radar_pi.mapa.tiles import MalhaTiles
    p = ROOT / "data/osm/malha_sp.sqlite"
    if not p.exists():
        pytest.skip("sem tiles")
    m = MalhaTiles(p, "sp")
    return m, Renderer(MapaBase(m), w, h, 1.5, True)


def test_n1_n9_icones_sentido_e_agrupamento():
    import pygame
    m, rend = _renderer(800, 480)
    cx, cy = to_xy(-23.55, -46.63)
    rad = [{"id": "a", "_x": cx, "_y": cy + 200, "_rumo": 0.0, "_conf": 0.9, "limite_kmh": 50},     # mesmo sentido
           {"id": "b", "_x": cx + 1, "_y": cy + 201, "_rumo": 5.0, "_conf": 0.9, "limite_kmh": 50},   # mesmo poste
           {"id": "c", "_x": cx + 40, "_y": cy + 200, "_rumo": 180.0, "_conf": 0.9, "limite_kmh": 50}]  # contramão
    M = rend._matriz(cx, cy, 0.0)
    g = rend._icones(M, rad, None, 0.0)
    por = {x["r"]["id"]: x for x in g}
    assert len(g) == 2 and por["a"]["n"] == 2 and not por["a"]["oposto"] and por["c"]["oposto"]
    sf = pygame.Surface((800, 480))
    rend._radares(sf, g, 0.0)


@pytest.mark.parametrize("tela", [(480, 320), (800, 480)])
def test_n3_auto_zoom_radar_cabe_na_tela(tela, registrar):
    m, rend = _renderer(*tela)
    cx, cy = to_xy(-23.55, -46.63)
    for _ in range(60):
        rend._auto_zoom(450.0, 0.05)
    ok = rend.radar_na_tela(cx, cy, 0.0, (cx, cy + 450))
    registrar(cenario="n3_auto_zoom", tela=f"{tela[0]}x{tela[1]}", zoom=round(rend.zoom, 2), zoom_base=round(rend.zoom_base, 2), radar_450m_visivel=ok)
    assert ok


def test_n7_velocidade_vermelha_pelo_osm():
    import pygame
    from radar_pi.mapa_ui.render import VERMELHO
    m, rend = _renderer(800, 480)
    s = rend.s
    px = (int(10 * s) + 1, int(10 * s) + int(41 * s))
    sf = pygame.Surface((800, 480))
    sf.fill((241, 243, 244))
    rend._hud(sf, 72, None, None, "", True, limite_via=60)
    assert tuple(sf.get_at(px))[:3] == VERMELHO
    sf.fill((241, 243, 244))
    rend._hud(sf, 55, None, None, "", True, limite_via=60)
    assert tuple(sf.get_at(px))[:3] != VERMELHO


def test_n5_tamoios_malha_completa():
    import sqlite3
    p = ROOT / "data/osm/malha_sp.sqlite"
    if not p.exists():
        pytest.skip("sem tiles")
    con = sqlite3.connect(p)
    x0, y0 = to_xy(-23.70, -45.95)
    x1, y1 = to_xy(-23.15, -45.35)
    n = con.execute("SELECT count(*) FROM arestas a JOIN nos n ON n.id=a.u WHERE a.highway IN ('residential','tertiary','unclassified') "
                    "AND n.x BETWEEN ? AND ? AND n.y BETWEEN ? AND ?", (x0, x1, y0, y1)).fetchone()[0]
    assert n > 1000


def test_n10_memoria_tiles(registrar):
    import psutil
    from radar_pi.mapa.tiles import MalhaTiles
    p = ROOT / "data/osm/malha_sp.sqlite"
    if not p.exists():
        pytest.skip("sem tiles")
    proc = psutil.Process()
    r0 = proc.memory_info().rss
    m = MalhaTiles(p, "sp")
    t0 = time.perf_counter()
    cargas_ms, celulas = [], []
    # "dirige" 25 km de oeste a leste pela cidade
    for k in range(0, 25001, 500):
        x, y = to_xy(-23.55, -46.80 + k / 101000.0)
        t1 = time.perf_counter()
        m.candidatos(x, y, 50)
        cargas_ms.append((time.perf_counter() - t1) * 1000)
        celulas.append(len(m.cel_arestas))
    rss = (proc.memory_info().rss - r0) / 1e6
    registrar(cenario="n10_tiles", delta_rss_MB=round(rss), celulas_max=max(celulas), arestas_carregadas=len(m.edges),
              carga_ms_max=round(max(cargas_ms)), carga_ms_p50=round(sorted(cargas_ms)[len(cargas_ms) // 2], 1),
              db_MB=round(os.path.getsize(p) / 1e6, 1))
    assert max(celulas) <= 61 and rss < 400
