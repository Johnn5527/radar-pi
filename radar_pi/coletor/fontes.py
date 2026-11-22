"""Um coletor por fonte oficial. Todos devolvem `Radar` no esquema único."""
from __future__ import annotations

import csv
import io
import json
import re
from pathlib import Path

import pandas as pd
import requests

from .base import Coletor, Radar, UA
from .util import limpa, num, parse_limite, parse_km, rodovia_norm, sem_acento

SEED = Path("/workspace/radares")

_CET_SENTIDO = re.compile(r"\(([^()]*?/[^()]*?)\)")


def cet_sentido(desc: str):
    """Extrai o sentido do texto CET, ex. '(CENTRO/BAIRRO)' -> 'CENTRO/BAIRRO'."""
    m = _CET_SENTIDO.search(desc or "")
    if not m:
        m2 = re.search(r"SENTIDO\s+([A-Z /\.]+)", sem_acento(desc or ""))
        return m2.group(1).strip() if m2 else None
    return sem_acento(m.group(1)).replace(" ", "")


def cet_via(desc: str):
    d = sem_acento(desc or "")
    d = re.split(r"\(|,| X | - |\bA MAIS\b|\bN[º°O]?\s*\d|\bNUMERO\b|\bALTURA\b|\bPROX", d)[0]
    return d.strip() or None


class CET(Coletor):
    nome = "cet_sp"
    url = "https://raw.githubusercontent.com/cetsp-dados/dados-fiscalizacao-eletronica/main/Base_Locais_Fiscalizados.csv"
    arquivo = "Base_Locais_Fiscalizados.csv"
    seed = SEED / "Base_Locais_Fiscalizados.csv"

    def parse(self, caminho):
        df = pd.read_csv(caminho, dtype=str)
        out = []
        for _, r in df.iterrows():
            lim, txt = parse_limite(r.get("VELOCIDADE"))
            desat = limpa(r.get("DESATIVAÇÃO"))
            desc = limpa(r.get("DESCRIÇÃO DO LOCAL"))
            out.append(Radar(
                fonte=self.nome, id_fonte=f"{limpa(r['CÓDIGO LOCAL'])}#{limpa(r.get('ATIVAÇÃO'))}",
                lat=num(r.get("LATITUDE")), lon=num(r.get("LONGITUDE")),
                limite_kmh=lim, limite_texto=txt, sentido=cet_sentido(desc), via=cet_via(desc),
                municipio="SAO PAULO", descricao=desc, tipo=(limpa(r.get("TIPO")) or "").strip(),
                status="inativo" if desat else "ativo", faixas=limpa(r.get("FAIXAS")),
                data_ativacao=limpa(r.get("ATIVAÇÃO")),
                extra={"serie": limpa(r.get("NÚMERO DE SÉRIE")), "desativacao": desat,
                       "metrologico": limpa(r.get("METROLÓGICO")),
                       "enquadramento": limpa(r.get("ENQUADRAMENTO"))}))
        return out


class DNIT(Coletor):
    nome = "dnit"
    url = ("https://servicos.dnit.gov.br/dadosabertos/dataset/afb1da02-221b-462b-a3e1-1b11dffb495b/"
           "resource/32ece134-15bf-40be-9582-d8889e71062e/download/cgpert___pncv___07_2026.xlsx")
    arquivo = "pncv.xlsx"
    seed = SEED / "amostras/dnit-pncv-07-2026.xlsx"
    uf = "SP"

    def parse(self, caminho):
        df = pd.read_excel(caminho)
        out = []
        for _, r in df.iterrows():
            if self.uf and limpa(r.get("UF")) != self.uf:
                continue
            lat = lon = None
            c = limpa(r.get("Coordenadas (Lat/Long)"))
            if c and "/" in c:
                a, b = c.split("/")
                lat, lon = num(a), num(b)
            fx = limpa(r.get("Faixas")) or ""
            sent = "AMBOS" if ("-C-" in fx and "-D-" in fx) else ("CRESCENTE" if "-C-" in fx else ("DECRESCENTE" if "-D-" in fx else None))
            out.append(Radar(
                fonte=self.nome, id_fonte=limpa(r.get("Código Identificação")), lat=lat, lon=lon,
                sentido=sent, via=rodovia_norm(r.get("Rodovia")), km=num(r.get("Km")),
                municipio=limpa(r.get("Município")), tipo=limpa(r.get("Tipo")), faixas=fx,
                data_ativacao=str(r.get("Início Operação"))[:10] if pd.notna(r.get("Início Operação")) else None))
        return out


class DERSP(Coletor):
    nome = "der_sp"
    url = "https://dersp.der.sp.gov.br/WebSite/Arquivos/DadosAbertos/AtivosRodoviarios/Radar/radares.xlsx"
    arquivo = "radares.xlsx"
    seed = SEED / "amostras/der-sp-radares.xlsx"

    def parse(self, caminho):
        df = pd.read_excel(caminho)
        out = []
        for _, r in df.iterrows():
            lat = lon = None
            c = limpa(r.get("Coordenadas"))
            if c and "," in c:
                a, b = c.split(",")[:2]
                lat, lon = num(a), num(b)
            fx = limpa(r.get("Faixas")) or ""
            letras = sorted(set(re.findall(r"\b([NSLO])-\d", fx)))
            sent = "AMBOS" if len(letras) > 1 else ({"N": "NORTE", "S": "SUL", "L": "LESTE", "O": "OESTE"}.get(letras[0]) if letras else None)
            out.append(Radar(
                fonte=self.nome, id_fonte=limpa(r.get("Código")), lat=lat, lon=lon, sentido=sent,
                via=rodovia_norm(r.get("Rodovia")), km=num(r.get("Km")), municipio=limpa(r.get("Município")),
                tipo=limpa(r.get("Tipo")), faixas=fx,
                status="inativo" if pd.notna(r.get("Cancelamento")) else "ativo",
                data_ativacao=str(r.get("Início Operação"))[:10] if pd.notna(r.get("Início Operação")) else None))
        return out


class ARTESP(Coletor):
    nome = "artesp"
    url = ("https://dadosabertos.artesp.sp.gov.br/dataset/491d79c5-ee09-4fe8-a3ce-1b425fe60bbe/"
           "resource/8b3212ff-1647-42ad-8a45-39e9934c92e1/download/recursos.xlsx")
    arquivo = "recursos.xlsx"
    seed = SEED / "amostras/artesp-recursos.xlsx"
    COMPONENTES = {"Medidor de Velocidade (Radar Fixo)": "FIXO",
                   "Medidor de Velocidade (Radar Estático)": "ESTATICO",
                   "Ponto Inativo de Operação de Radar Fixo": "PONTO_INATIVO"}

    def parse(self, caminho):
        df = pd.read_excel(caminho)
        df = df[df["Desc_Componente"].isin(self.COMPONENTES)]
        out = []
        for _, r in df.iterrows():
            tipo = self.COMPONENTES[r["Desc_Componente"]]
            st = limpa(r.get("Cod_Status")) or ""
            ativo = st in ("Ativo", "Sem Telemetria") and tipo != "PONTO_INATIVO"
            sent = sem_acento(limpa(r.get("Sentido")) or "") or None
            out.append(Radar(
                fonte=self.nome, id_fonte=str(r.get("ID")), lat=num(r.get("Latitude")), lon=num(r.get("Longitude")),
                sentido=sent, via=rodovia_norm(r.get("Rodovia")), km=parse_km(r.get("Localizacao")),
                municipio=limpa(r.get("Municipio")), tipo=tipo, status="ativo" if ativo else "inativo",
                data_ativacao=str(r.get("Data_Operacao"))[:10] if pd.notna(r.get("Data_Operacao")) else None,
                extra={"concessionaria": limpa(r.get("Nome_Concessionaria")), "cod_status": st,
                       "marginal": limpa(r.get("Ind_Marginal")), "tunel": limpa(r.get("Tunel"))}))
        return out


class MOGI(Coletor):
    nome = "mogi"
    url = ("https://dados.mogidascruzes.sp.gov.br/dataset/b93af036-8ed9-4442-82e4-7bc8c3d6c64f"
           "/resource/822a6c76-4c9b-413b-8299-abd40f903bf4/download/localizacao_radares2.csv")
    arquivo = "localizacao_radares2.csv"
    seed = SEED / "amostras/mogi-radares.csv"

    def parse(self, caminho):
        txt = Path(caminho).read_bytes().decode("latin-1")
        out = []
        for r in csv.DictReader(io.StringIO(txt), delimiter=";"):
            lim, ltxt = parse_limite(r.get("Velocidade"))
            st = sem_acento(r.get("Status") or "")
            out.append(Radar(
                fonte=self.nome, id_fonte=limpa(r.get("id_radar")), lat=num(r.get("latitude")), lon=num(r.get("longitude")),
                limite_kmh=lim, limite_texto=ltxt, sentido=sem_acento(limpa(r.get("Sentido")) or "").replace("-", "/") or None,
                via=sem_acento(limpa(r.get("logradouro2")) or "") or None, municipio="MOGI DAS CRUZES",
                descricao=limpa(r.get("Logradouro")), tipo=limpa(r.get("subtipo")),
                status="ativo" if "OPERA" in st else ("inativo" if st else "desconhecido")))
        return out


class ESPELHO_PARDAL(Coletor):
    """Espelho público (github.com/Murkiriel/pardal, estados/SP/radares.csv) que já cruzou o Inmetro
    com DER-SP/ANTT/DNIT. NÃO é fonte oficial direta: usado só para limite, marcado como
    'inmetro_via_espelho', quando o Inmetro (RBMLQ) bloqueia o download (403)."""
    nome = "espelho_pardal"
    url = "https://raw.githubusercontent.com/Murkiriel/pardal/main/estados/SP/radares.csv"
    arquivo = "radares_SP.csv"
    seed = Path(__file__).resolve().parents[2] / "data/raw/pardal_espelho/radares_SP.csv"

    def parse(self, caminho):
        out = []
        for i, r in enumerate(csv.DictReader(open(caminho, encoding="utf-8"))):
            src = r.get("source") or ""
            if not any(k in src for k in ("INMETRO", "DER-SP", "ANTT", "DNIT")):
                continue
            if not r.get("limit_kmh") and "ANTT" not in src:
                continue
            lim = int(r["limit_kmh"]) if r.get("limit_kmh") else None
            out.append(Radar(fonte=self.nome, id_fonte=str(i), lat=num(r["lat"]), lon=num(r["lng"]),
                             limite_kmh=lim, limite_texto=r["limit_kmh"] or None, tipo=r.get("kind"),
                             sentido=f"RUMO:{r['direction_deg']}" if r.get("direction_deg") else None,
                             status="ativo" if r.get("active") == "1" else "inativo", extra={"source": src}))
        return out


class INMETRO(Coletor):
    """PSIE/RBMLQ: lista nacional de medidores aferidos (sem coordenada)."""
    nome = "inmetro"
    url = "https://servicos.rbmlq.gov.br/dados-abertos/SP/medidores.json"
    arquivo = "medidores_SP.json"
    seed = None

    def parse(self, caminho):
        dados = json.loads(Path(caminho).read_text(encoding="utf-8"))
        itens = dados if isinstance(dados, list) else next((v for v in dados.values() if isinstance(v, list)), [])
        out = []
        for i, it in enumerate(itens):
            if "LocalVerificacao" in it:  # formato documentado do PSIE
                vel = [int(f.get("VelocidadeNominal")) for f in it.get("Faixas") or []
                       if str(f.get("VelocidadeNominal") or "").strip().isdigit()]
                loc = str(it.get("LocalVerificacao") or "")
                out.append(Radar(fonte=self.nome, id_fonte=str(it.get("NumeroSerie") or i), lat=None, lon=None,
                                 limite_kmh=min(vel) if vel else None, limite_texto=str(min(vel)) if vel else None,
                                 via=rodovia_norm(loc.split(",")[0]) if re.match(r"\s*(SP|BR)", loc) else None,
                                 km=parse_km(loc), municipio=it.get("Municipio"), descricao=loc,
                                 extra={"validade": it.get("DataValidade"), "resultado": it.get("UltimoResultado"),
                                        "numero_serie": it.get("NumeroSerie")}))
                continue
            g = {sem_acento(k).lower(): v for k, v in it.items()}
            pick = lambda *ks: next((g[k] for k in g for p in ks if p in k), None)  # noqa: E731
            lim, ltxt = parse_limite(str(pick("velocidade", "limite") or ""))
            out.append(Radar(fonte=self.nome, id_fonte=str(pick("serie", "numero", "id") or i), lat=None, lon=None,
                             limite_kmh=lim, limite_texto=ltxt, via=rodovia_norm(pick("rodovia")),
                             km=num(pick("km")), municipio=pick("municipio"), descricao=str(pick("local", "endereco") or ""),
                             extra={"validade": pick("validade", "vencimento"), "bruto": it}))
        return out


class ANTT(Coletor):
    """ANTT (concessões federais): CKAN package_show -> recurso mais recente."""
    nome = "antt"
    url = "https://dados.antt.gov.br/api/3/action/package_show?id=radar"
    arquivo = "radar_antt.csv"
    seed = None
    timeout = 30
    tentativas = 2

    def _baixar_uma(self, url):
        meta = json.loads(super()._baixar_uma(url))
        return super()._baixar_uma(self._recurso(meta))

    def _baixar_curl(self, url):
        meta = json.loads(super()._baixar_curl(url))
        return super()._baixar_curl(self._recurso(meta))

    @staticmethod
    def _recurso(meta):
        recs = [r for r in meta["result"]["resources"] if (r.get("format") or "").upper() in ("CSV", "XLSX", "JSON")]
        recs.sort(key=lambda r: r.get("last_modified") or r.get("created") or "")
        return recs[-1]["url"]

    def parse(self, caminho):
        raw = Path(caminho).read_bytes()
        txt = raw.decode("utf-8", errors="replace")
        sep = ";" if txt.count(";") > txt.count(",") else ","
        out = []
        for i, r in enumerate(csv.DictReader(io.StringIO(txt), delimiter=sep)):
            g = {sem_acento(k).lower(): v for k, v in r.items() if k}
            pick = lambda *ks: next((g[k] for k in g for p in ks if p in k), None)  # noqa: E731
            uf = (pick("uf") or "").strip().upper()
            if uf and uf != "SP":
                continue
            lim, ltxt = parse_limite(pick("velocidade", "limite"))
            out.append(Radar(fonte=self.nome, id_fonte=str(i), lat=num(pick("latitude")), lon=num(pick("longitude")),
                             limite_kmh=lim, limite_texto=ltxt, via=rodovia_norm(pick("rodovia")), km=num(pick("km")),
                             sentido=sem_acento(pick("sentido") or "") or None, municipio=pick("municipio"),
                             tipo=pick("tipo")))
        return out


TODOS = [CET, DERSP, ARTESP, DNIT, ANTT, INMETRO, ESPELHO_PARDAL, MOGI]
PRIORIDADE = {"espelho_pardal": 99, "antt_via_espelho": 6, "cet_sp": 0, "mogi": 1, "der_sp": 2, "artesp": 3, "antt": 4, "dnit": 5, "inmetro": 9}
