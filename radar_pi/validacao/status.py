"""Estado de validação de cada radar: confirmado (2+ fontes) / nao_verificado / conflito.

Regras (decisão do Johnny):
- OSM só VALIDA: conta como fonte extra quando há radar OSM a <= 40 m, mas nunca cria radar.
  Radar só no OSM ('so_osm') vai para a tabela revisao_osm — não entra em `radares`, logo não alerta.
- Fontes contadas: cada fonte oficial distinta no mesmo ponto (duplicatas entre fontes, <= 30 m),
  Inmetro (limite casado por série/km ou via espelho) e OSM. Registro cuja origem já é OSM
  (ANTT+OSM do espelho) não conta o OSM de novo (não é independente).
- conflito: fontes discordam no limite, ou uma fonte oficial dá o ponto como inativo e outra como ativo.
- Este módulo NUNCA altera `status` nem remove radar: só remoção por evidência oficial (ex.: CET desativado),
  que já vem das próprias fontes. Ausência no OSM = 'nao_verificado', jamais 'não existe'.
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import date

from radar_pi.validacao import osm as V

CONFIRMADO, NAO_VERIFICADO, CONFLITO = "confirmado", "nao_verificado", "conflito"


def _derivado_de_osm(r) -> bool:
    try:
        ex = json.loads(r["extra"]) if isinstance(r["extra"], str) else (r["extra"] or {})
    except (TypeError, ValueError):
        ex = {}
    return "OSM" in str(ex.get("source", ""))


def classificar(radares: list[dict], osm: list[dict], raio=40.0, raio_proximo=150.0, raio_inativo=30.0):
    """radares: linhas da tabela (dicts). Retorna (atualizacoes por id, so_osm)."""
    ativos = [r for r in radares if r["status"] == "ativo" and r.get("lat") is not None]
    canon = [r for r in ativos if not r.get("duplicata_de")]
    dups = defaultdict(list)
    for r in ativos:
        if r.get("duplicata_de"):
            dups[r["duplicata_de"]].append(r)
    base = [{"id": r["id"], "lat": r["lat"], "lon": r["lon"], "limite": r.get("limite_kmh")} for r in canon]
    linhas, so_osm, invalidas = V.cruzar(base, osm, raio, raio_proximo)
    por_id = {l["id"]: l for l in linhas}
    inativos = V.Grade([r for r in radares if r["status"] != "ativo" and V.coord_valida(r.get("lat"), r.get("lon"))])
    upd = {}
    for r in canon:
        grupo = [r] + dups.get(r["id"], [])
        fontes = {g["fonte"] for g in grupo}
        if any(g.get("limite_origem") in ("inmetro", "inmetro_via_espelho") for g in grupo):
            fontes.add("inmetro")
        motivos = []
        l = por_id.get(r["id"])
        if l is None:
            motivos.append("coordenada_invalida")
        elif l["categoria"] == "confirmado_osm" and not _derivado_de_osm(r):
            fontes.add("osm")
        lims = {g["limite_kmh"] for g in grupo if g.get("limite_kmh")}
        if len(lims) > 1:
            motivos.append("limite_divergente_fontes:" + "/".join(map(str, sorted(lims))))
        if l and l["categoria"] == "confirmado_osm" and l["limite_divergente"]:
            motivos.append(f"limite_divergente_osm:{l['limite_base']}x{l['limite_osm']}")
        if l and any(o["fonte"] != r["fonte"] for _, o in inativos.perto(r["lat"], r["lon"], raio_inativo)):
            motivos.append("inativo_em_outra_fonte")
        conflito = any(not m.startswith("coordenada") for m in motivos)
        st = CONFLITO if conflito else (CONFIRMADO if len(fontes) >= 2 else NAO_VERIFICADO)
        upd[r["id"]] = {"status_validacao": st, "n_fontes": len(fontes), "fontes_validacao": ",".join(sorted(fontes)),
                        "osm_id": l and l["osm_id"], "osm_dist_m": l and l["dist_m"],
                        "osm_limite": l and l["limite_osm"], "motivo_validacao": ";".join(motivos) or None,
                        "categoria_osm": l["categoria"] if l else "coord_invalida"}
        for d in dups.get(r["id"], []):     # duplicatas herdam o estado do canônico
            upd[d["id"]] = dict(upd[r["id"]], duplicata=True)
    return upd, so_osm


def aplicar(con, osm: list[dict], origem="?", raio=40.0, raio_proximo=150.0) -> dict:
    rows = [dict(r) for r in con.execute("SELECT * FROM radares")]
    upd, so_osm = classificar(rows, osm, raio, raio_proximo)
    con.execute("UPDATE radares SET status_validacao=NULL, n_fontes=NULL, fontes_validacao=NULL, osm_id=NULL, "
                "osm_dist_m=NULL, osm_limite=NULL, motivo_validacao=NULL")
    con.executemany("UPDATE radares SET status_validacao=?, n_fontes=?, fontes_validacao=?, osm_id=?, osm_dist_m=?, "
                    "osm_limite=?, motivo_validacao=? WHERE id=?",
                    [(u["status_validacao"], u["n_fontes"], u["fontes_validacao"], u["osm_id"], u["osm_dist_m"],
                      u["osm_limite"], u["motivo_validacao"], i) for i, u in upd.items()])
    con.execute("DELETE FROM revisao_osm")
    hoje = date.today().isoformat()
    con.executemany("INSERT OR REPLACE INTO revisao_osm VALUES(?,?,?,?,?,?,?)",
                    [(o["osm_id"], o["lat"], o["lon"], o["limite_osm"], "so_osm: candidato a radar novo ou desativado; "
                      "conferir antes de adicionar", origem, hoje) for o in so_osm])
    con.commit()
    from collections import Counter
    canon = {i: u for i, u in upd.items() if not u.get("duplicata")}
    return {"status": dict(Counter(u["status_validacao"] for u in canon.values())),
            "categoria_osm": dict(Counter(u["categoria_osm"] for u in canon.values())),
            "revisao_osm": len(so_osm), "radares_osm": len(osm), "origem_osm": origem, "radares_avaliados": len(canon),
            "n_fontes": dict(Counter(u["n_fontes"] for u in canon.values())),
            "limite_divergente_osm": sum(1 for u in canon.values() if "limite_divergente_osm" in (u["motivo_validacao"] or "")),
            "limite_divergente_fontes": sum(1 for u in canon.values() if "limite_divergente_fontes" in (u["motivo_validacao"] or "")),
            "inativo_em_outra_fonte": sum(1 for u in canon.values() if "inativo_em_outra_fonte" in (u["motivo_validacao"] or ""))}
