import math
import re
import unicodedata


def limpa(s):
    if s is None or (isinstance(s, float) and math.isnan(s)):
        return None
    s = str(s).strip()
    return s or None


def num(s):
    s = limpa(s)
    if s is None:
        return None
    try:
        return float(s.replace(",", "."))
    except ValueError:
        return None


def sem_acento(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s or "") if not unicodedata.combining(c)).upper()


def parse_limite(txt):
    """'90/60 km/h' -> (90, '90/60'). Primeiro valor = veículos leves (premissa, ver relatório)."""
    txt = limpa(txt)
    if not txt:
        return None, None
    nums = [int(n) for n in re.findall(r"\d+", txt)]
    if not nums:
        return None, None
    return nums[0], "/".join(str(n) for n in nums)


def parse_km(txt):
    txt = limpa(txt)
    if not txt:
        return None
    m = re.search(r"(\d+)\s*\+\s*(\d+)", txt)
    if m:
        return int(m.group(1)) + int(m.group(2)) / 1000
    return num(re.sub(r"[^\d,\.]", "", txt))


def rodovia_norm(txt):
    """'SP 008' / 'SP099' / 364 -> 'SP-008' / 'SP-099' / 'BR-364'"""
    txt = limpa(txt)
    if not txt:
        return None
    t = sem_acento(txt).replace(" ", "").replace("-", "")
    m = re.match(r"^(SPA|SPI|SPM|SP|BR)?(\d{1,3})", t)
    if not m:
        return txt
    pref = m.group(1) or "BR"
    return f"{pref}-{int(m.group(2)):03d}"
