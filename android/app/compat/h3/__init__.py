"""Substituto do h3 SÓ para o APK Android (o h3 nativo não tem receita no python-for-android).

NÃO é H3 de verdade: é uma grade quadrada em graus com a mesma área média das resoluções H3 usadas
(res 7 ≈ 5,16 km², res 9 ≈ 0,105 km²). Os bancos empacotados no APK são re-indexados com esta mesma grade
(android/preparar.py), então as consultas por anel (grid_disk) continuam coerentes. Fora do APK o
projeto usa o h3 real."""
import math

_AREA_KM2 = {5: 252.9, 6: 36.13, 7: 5.161, 8: 0.7373, 9: 0.1053, 10: 0.01504}
_COS = math.cos(math.radians(-22.5))   # latitude média de SP


def _lado_graus(res):
    lado_m = math.sqrt(_AREA_KM2[res]) * 1000.0
    return lado_m / 111320.0, lado_m / (111320.0 * _COS)


def latlng_to_cell(lat, lon, res):
    dlat, dlon = _lado_graus(res)
    return f"q{res}_{math.floor(lat / dlat)}_{math.floor(lon / dlon)}"


def grid_disk(cell, k=1):
    res, i, j = cell[1:].split("_")
    i, j = int(i), int(j)
    return [f"q{res}_{i + a}_{j + b}" for a in range(-k, k + 1) for b in range(-k, k + 1)]


def cell_to_latlng(cell):
    res, i, j = cell[1:].split("_")
    dlat, dlon = _lado_graus(int(res))
    return (int(i) + 0.5) * dlat, (int(j) + 0.5) * dlon
