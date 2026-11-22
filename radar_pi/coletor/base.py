"""Base dos coletores: download com proveniência e fallback para a versão anterior."""
from __future__ import annotations

import hashlib
import json
import shutil
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

import requests

from radar_pi import config

UA = "Mozilla/5.0 (X11; Linux x86_64) radar-pi/0.1 (+uso pessoal, dados abertos)"
CAMPOS = ["id", "fonte", "id_fonte", "lat", "lon", "limite_kmh", "limite_texto", "sentido",
          "via", "km", "municipio", "descricao", "tipo", "status", "faixas",
          "data_ativacao", "data_coleta"]


@dataclass
class Radar:
    fonte: str
    id_fonte: str
    lat: Optional[float]
    lon: Optional[float]
    limite_kmh: Optional[int] = None
    limite_texto: Optional[str] = None
    sentido: Optional[str] = None
    via: Optional[str] = None
    km: Optional[float] = None
    municipio: Optional[str] = None
    descricao: Optional[str] = None
    tipo: Optional[str] = None
    status: str = "ativo"            # ativo | inativo | desconhecido
    limite_origem: Optional[str] = None  # oficial | inmetro | inmetro_via_espelho
    faixas: Optional[str] = None
    data_ativacao: Optional[str] = None
    data_coleta: Optional[str] = None
    extra: dict = field(default_factory=dict)

    @property
    def id(self) -> str:
        return f"{self.fonte}:{self.id_fonte}"

    def as_row(self) -> dict:
        d = asdict(self)
        d["id"] = self.id
        d["extra"] = json.dumps(self.extra, ensure_ascii=False, default=str)
        return d


@dataclass
class Proveniencia:
    fonte: str
    url: str
    arquivo: str
    sha256: str
    data_coleta: str
    status_coleta: str       # ok | falhou_usando_anterior | falhou_sem_dados
    erro: str = ""
    registros: int = 0


class Coletor:
    nome = "base"
    url = ""
    arquivo = "dados.bin"
    seed: Optional[Path] = None      # cópia local já baixada (versão anterior inicial)
    timeout = 60

    def __init__(self, raw_dir: Path | None = None, offline: bool = False):
        self.dir = Path(raw_dir or config.RAW_DIR) / self.nome
        self.dir.mkdir(parents=True, exist_ok=True)
        self.offline = offline

    # --- download -----------------------------------------------------------
    def _versoes(self):
        return sorted(self.dir.glob(f"*__{self.arquivo}"))

    tentativas = 3

    def _baixar_uma(self, url) -> bytes:
        r = requests.get(url, headers={"User-Agent": UA, "Accept": "*/*"}, timeout=self.timeout)
        r.raise_for_status()
        return r.content

    def _baixar_curl(self, url) -> bytes:
        """Fallback com curl (outra pilha TLS; resolve servidores com TLS antigo). Nunca desliga a verificação."""
        import subprocess
        p = subprocess.run(["curl", "-sS", "-L", "--fail", "-m", str(self.timeout), "-A", UA,
                            "--ciphers", "DEFAULT@SECLEVEL=1", url], capture_output=True)
        if p.returncode != 0:
            raise IOError(f"curl {p.returncode}: {p.stderr.decode(errors='replace')[:150]}")
        return p.stdout

    def _baixar(self) -> bytes:
        erros = []
        for n in range(self.tentativas):
            for f in (self._baixar_uma, self._baixar_curl):
                try:
                    c = f(self.url)
                    if len(c) < 200:
                        raise ValueError(f"resposta pequena demais ({len(c)} bytes)")
                    self.validar_bruto(c)
                    return c
                except Exception as e:  # noqa: BLE001
                    erros.append(f"{f.__name__}#{n + 1}: {type(e).__name__}: {str(e)[:90]}")
                    if "403" in str(e) or "401" in str(e):
                        raise IOError(" | ".join(erros))  # bloqueio: não insistir
            time.sleep(2 * (n + 1))
        raise IOError(" | ".join(erros[-3:]))

    def validar_bruto(self, conteudo: bytes):
        if conteudo[:200].lower().lstrip().startswith((b"<!doctype html", b"<html")):
            raise ValueError("servidor devolveu HTML (bloqueio/erro), não o dado")

    def obter(self) -> tuple[Optional[Path], Proveniencia]:
        agora = datetime.now().isoformat(timespec="seconds")
        erro = ""
        if not self.offline and self.url:
            try:
                dados = self._baixar()
                destino = self.dir / f"{time.strftime('%Y%m%dT%H%M%S')}__{self.arquivo}"
                destino.write_bytes(dados)
                return destino, Proveniencia(self.nome, self.url, str(destino),
                                             hashlib.sha256(dados).hexdigest(), agora, "ok")
            except Exception as e:  # noqa: BLE001 - qualquer falha cai no fallback
                erro = f"{type(e).__name__}: {e}"[:300]
        elif self.offline:
            erro = "modo offline"
        versoes = self._versoes()
        if not versoes and self.seed and Path(self.seed).exists():
            destino = self.dir / f"seed__{self.arquivo}"
            shutil.copy(self.seed, destino)
            versoes = [destino]
        if versoes:
            ult = versoes[-1]
            return ult, Proveniencia(self.nome, self.url, str(ult),
                                     hashlib.sha256(ult.read_bytes()).hexdigest(),
                                     agora, "falhou_usando_anterior", erro)
        return None, Proveniencia(self.nome, self.url, "", "", agora, "falhou_sem_dados", erro)

    def parse(self, caminho: Path) -> list[Radar]:
        raise NotImplementedError

    def coletar(self) -> tuple[list[Radar], Proveniencia]:
        caminho, prov = self.obter()
        if caminho is None:
            return [], prov
        try:
            radares = self.parse(caminho)
        except Exception as e:  # noqa: BLE001
            prov.status_coleta = "falhou_parse"
            prov.erro = f"{type(e).__name__}: {e}"[:300]
            return [], prov
        for r in radares:
            r.data_coleta = prov.data_coleta
        prov.registros = len(radares)
        return radares, prov
