"""Testes de unidade: parsing, coletor com fallback, H3, voz."""
import shutil
import sqlite3

import h3

from radar_pi.coletor.base import Coletor, Radar
from radar_pi.coletor.fontes import cet_sentido, cet_via
from radar_pi.coletor.util import parse_limite, parse_km, rodovia_norm
from radar_pi.dados import db
from radar_pi.voz.tts import Voz


def test_parse_limite():
    assert parse_limite("90/60 km/h") == (90, "90/60")
    assert parse_limite("60/50/50 km/h") == (60, "60/50/50")
    assert parse_limite("50 KM/H") == (50, "50")
    assert parse_limite(None) == (None, None)


def test_cet_sentido_e_via():
    assert cet_sentido("AV. INTERLAGOS (CENTRO/BAIRRO) A MAIS 11 METROS") == "CENTRO/BAIRRO"
    assert cet_via("AV. INTERLAGOS (CENTRO/BAIRRO) A MAIS 11 METROS") == "AV. INTERLAGOS"


def test_cet_sentido_abreviado_c_b(registrar):
    """A CET também escreve (C/B) e (B/C). O vínculo só entende CENTRO/BAIRRO por extenso."""
    from radar_pi.mapa.vinculo import sentido_alvo
    modo, _, _ = sentido_alvo({"sentido": cet_sentido("R X (C/B) NUMERO 10"), "municipio": "SAO PAULO"}, 333000, 7394000)
    registrar(cenario="abreviacao_cb", modo=modo)
    assert modo == "rumo", f"(C/B) virou '{modo}' => vinculado aos dois sentidos"


def test_rodovia_km():
    assert rodovia_norm("SP 008") == "SP-008" and rodovia_norm("SP099") == "SP-099" and rodovia_norm(364) == "BR-364"
    assert parse_km("km 035+000") == 35.0 and parse_km("km 109+800") == 109.8


class _Fake(Coletor):
    nome = "fake"
    url = "https://exemplo.invalido/radares.csv"
    arquivo = "r.csv"
    timeout = 3

    def parse(self, caminho):
        return [Radar("fake", l.split(",")[0], float(l.split(",")[1]), float(l.split(",")[2]))
                for l in open(caminho).read().splitlines()[1:]]


def test_coletor_falha_mantem_versao_anterior(tmp_path, registrar):
    d = tmp_path / "fake"
    d.mkdir()
    (d / "20260101T000000__r.csv").write_text("id,lat,lon\n1,-23.5,-46.6\n2,-23.6,-46.7\n")
    rad, prov = _Fake(raw_dir=tmp_path).coletar()
    registrar(cenario="fallback", status=prov.status_coleta, registros=len(rad), erro=prov.erro[:80])
    assert prov.status_coleta == "falhou_usando_anterior" and len(rad) == 2


def test_coletor_falha_sem_versao(tmp_path):
    rad, prov = _Fake(raw_dir=tmp_path).coletar()
    assert prov.status_coleta == "falhou_sem_dados" and rad == []


def test_coletor_rejeita_html_de_bloqueio(tmp_path):
    """Servidor que devolve página HTML de erro com status 200 não pode sobrescrever o dado."""
    c = _Fake(raw_dir=tmp_path)
    try:
        c.validar_bruto(b"<!DOCTYPE html><html><title>403 - Proibido</title></html>" + b" " * 300)
        assert False, "aceitou HTML"
    except ValueError:
        pass


def test_h3_consulta_vizinhanca():
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript(db.SCHEMA)
    db.gravar_radares(con, [{"id": "x:1", "fonte": "x", "lat": -23.55, "lon": -46.63, "status": "ativo"},
                            {"id": "x:2", "fonte": "x", "lat": -23.56, "lon": -46.63, "status": "ativo"},
                            {"id": "x:3", "fonte": "x", "lat": -23.65, "lon": -46.63, "status": "ativo"}])
    ids = {r["id"] for r in db.radares_proximos(con, -23.55, -46.63, k=4)}
    assert ids == {"x:1", "x:2"}
    assert h3.get_resolution(db.celula(-23.55, -46.63)) == 9


def test_voz_espeak(tmp_path, registrar):
    v = Voz(pasta=tmp_path, tts="espeak", cache_dir=tmp_path / "cache")
    r = v.falar(Voz.frase(60))
    registrar(cenario="voz", disponivel=v.disponivel, ms=r["ms"], wav=bool(r["wav"]), erro=r["erro"],
              bytes=(tmp_path / r["wav"].split("/")[-1]).stat().st_size if r["wav"] else 0)
    assert r["texto"] == "radar, 60" and r["wav"] and not r["erro"]
