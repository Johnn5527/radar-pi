"""Banco SQLite com índice espacial H3 (hexágonos da Uber)."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import h3

from radar_pi import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS radares(
  id TEXT PRIMARY KEY, fonte TEXT, id_fonte TEXT, lat REAL, lon REAL,
  limite_kmh INTEGER, limite_texto TEXT, sentido TEXT, via TEXT, km REAL, municipio TEXT,
  descricao TEXT, tipo TEXT, status TEXT, faixas TEXT, data_ativacao TEXT, data_coleta TEXT,
  extra TEXT, coord_origem TEXT, duplicata_de TEXT, h3 TEXT, limite_origem TEXT);
CREATE INDEX IF NOT EXISTS ix_radares_h3 ON radares(h3);
CREATE TABLE IF NOT EXISTS fontes(
  fonte TEXT, url TEXT, arquivo TEXT, sha256 TEXT, data_coleta TEXT, status_coleta TEXT,
  erro TEXT, registros INTEGER);
CREATE TABLE IF NOT EXISTS vinculos(
  radar_id TEXT, grafo TEXT, u INTEGER, v INTEGER, k INTEGER, offset_m REAL, dist_snap_m REAL,
  nome_via TEXT, metodo TEXT, confianca REAL, rumo REAL, limite_osm INTEGER);
CREATE INDEX IF NOT EXISTS ix_vinc_radar ON vinculos(radar_id);
CREATE INDEX IF NOT EXISTS ix_vinc_grafo ON vinculos(grafo);
CREATE TABLE IF NOT EXISTS revisao_osm(
  osm_id TEXT PRIMARY KEY, lat REAL, lon REAL, limite_osm INTEGER, motivo TEXT, origem TEXT, data TEXT);
"""
# colunas de validação (só banco/relatório; a UI e o alerta não usam): migração aditiva
COLS_VALIDACAO = {"status_validacao": "TEXT", "n_fontes": "INTEGER", "fontes_validacao": "TEXT",
                  "osm_id": "TEXT", "osm_dist_m": "REAL", "osm_limite": "INTEGER", "motivo_validacao": "TEXT"}

COLS = ["id", "fonte", "id_fonte", "lat", "lon", "limite_kmh", "limite_texto", "sentido", "via", "km",
        "municipio", "descricao", "tipo", "status", "faixas", "data_ativacao", "data_coleta", "extra",
        "coord_origem", "duplicata_de", "h3", "limite_origem"]


def conectar(path: Path | str | None = None) -> sqlite3.Connection:
    con = sqlite3.connect(str(path or config.DB_PATH))
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    tem = {r[1] for r in con.execute("PRAGMA table_info(radares)")}
    for c, t in COLS_VALIDACAO.items():
        if c not in tem:
            con.execute(f"ALTER TABLE radares ADD COLUMN {c} {t}")
    return con


def celula(lat, lon, res=None):
    if lat is None or lon is None:
        return None
    return h3.latlng_to_cell(lat, lon, res or config.H3_RES)


def gravar_radares(con, rows: list[dict], res=None):
    for r in rows:
        r["h3"] = celula(r.get("lat"), r.get("lon"), res)
        r.setdefault("coord_origem", "fonte" if r.get("lat") is not None else None)
        r.setdefault("duplicata_de", None)
    con.executemany(f"INSERT OR REPLACE INTO radares({','.join(COLS)}) VALUES({','.join('?' * len(COLS))})",
                    [[r.get(c) for c in COLS] for r in rows])
    con.commit()


def radares_proximos(con, lat, lon, k=2, res=None, so_ativos=True):
    """Consulta por anel de hexágonos H3 em volta da posição (res 9 ≈ 174 m de aresta)."""
    cells = list(h3.grid_disk(celula(lat, lon, res), k))
    q = f"SELECT * FROM radares WHERE h3 IN ({','.join('?' * len(cells))})"
    if so_ativos:
        q += " AND status='ativo' AND duplicata_de IS NULL"
    return [dict(r) for r in con.execute(q, cells)]
