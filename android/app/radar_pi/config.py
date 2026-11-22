"""Configuração via variáveis de ambiente (ver .env.example)."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv():
    f = ROOT / ".env"
    if f.exists():
        for line in f.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


_load_dotenv()


def _p(name, default):
    v = Path(os.environ.get(name, default))
    return v if v.is_absolute() else ROOT / v


DB_PATH = _p("RADAR_DB_PATH", "data/radares.sqlite")
RAW_DIR = _p("RADAR_RAW_DIR", "data/raw")
GRAPH_PATH = _p("RADAR_GRAPH_PATH", "data/osm/sp_city.graphml")
ALERTA_M = float(os.environ.get("RADAR_ALERTA_M", 450))
HORIZONTE_M = float(os.environ.get("RADAR_HORIZONTE_M", 700))
H3_RES = int(os.environ.get("RADAR_H3_RES", 9))
VOZ = os.environ.get("RADAR_VOZ", "espeak-ng")
VOZ_DIR = _p("RADAR_VOZ_DIR", "logs/voz")
GPS_SIGMA_M = float(os.environ.get("RADAR_GPS_SIGMA_M", 10))

# --- tela do mapa ---
_tela = os.environ.get("RADAR_TELA", "800x480").lower().split("x")
TELA_W, TELA_H = int(_tela[0]), int(_tela[1])
FULLSCREEN = os.environ.get("RADAR_FULLSCREEN", "0") == "1"
FPS = int(os.environ.get("RADAR_FPS", 20))
ZOOM_M_PX = float(os.environ.get("RADAR_ZOOM_M_PX", 1.5))
ROTACIONAR = os.environ.get("RADAR_ROTACIONAR", "1") == "1"
HORIZONTE_PLACA_M = float(os.environ.get("RADAR_HORIZONTE_PLACA_M", 2000))
GPS = os.environ.get("RADAR_GPS", "sim")
GPSD_HOST = os.environ.get("RADAR_GPSD_HOST", "127.0.0.1")
GPSD_PORT = int(os.environ.get("RADAR_GPSD_PORT", 2947))
# --- v2 (correções) ---
GRAPH_TILES = _p("RADAR_GRAPH_TILES", "data/osm/malha_sp.sqlite")
VEICULO = os.environ.get("RADAR_VEICULO", "leve").lower()        # E11: leve|pesado (limite "90/60")
ALERTA_HIST_M = float(os.environ.get("RADAR_ALERTA_HIST_M", 30))  # E5: aviso termina só acima de ALERTA_M+30
ALERTA_SEGURA_S = float(os.environ.get("RADAR_ALERTA_SEGURA_S", 3))  # E5: segura o aviso se o alvo some por <3 s
# --- voz (Piper TTS, OHF-Voice/piper1-gpl) ---
TTS = os.environ.get("RADAR_TTS", "piper").lower()                      # piper|espeak (espeak = fallback)
PIPER_VOZ = _p("RADAR_PIPER_VOZ", "data/vozes/pt_BR-faber-medium.onnx")
PIPER_LENGTH_SCALE = float(os.environ.get("RADAR_PIPER_LENGTH_SCALE", 0.9))  # <1 = mais rápido
VOZ_CACHE = _p("RADAR_VOZ_CACHE", "data/voz_cache")
# --- validação (OSM só valida; telemetria local anonimizada) ---
OSM_RADARES = _p("RADAR_OSM_RADARES", "data/raw/osm/radares_osm.json")   # gerado por scripts/cruzar_osm.py --salvar-osm
TELEMETRIA_DB = _p("RADAR_TELEMETRIA_DB", "data/telemetria.sqlite")
TELEMETRIA = os.environ.get("RADAR_TELEMETRIA", "1") == "1"
TELEMETRIA_K_MIN = int(os.environ.get("RADAR_TELEMETRIA_K_MIN", 5))      # k-anonimato: célula só vale com >=k passagens
