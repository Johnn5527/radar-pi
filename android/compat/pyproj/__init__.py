"""Substituto mínimo do pyproj, SÓ para o APK Android (o pyproj real não compila no python-for-android).

Implementa apenas o que o radar-pi usa: EPSG:4326 <-> EPSG:31983 (SIRGAS 2000 / UTM 23S, elipsoide GRS80),
com as séries de Krüger de 6ª ordem (Karney 2011): erro < 1 mm dentro da zona. Validado contra o pyproj real
em android/tests/test_compat.py."""
import math

_A = 6378137.0
_F = 1 / 298.257222101
_K0 = 0.9996
_LON0 = math.radians(-45.0)
_FE, _FN = 500000.0, 10000000.0
_N = _F / (2 - _F)
_N2, _N3, _N4, _N5, _N6 = _N ** 2, _N ** 3, _N ** 4, _N ** 5, _N ** 6
_AA = _A / (1 + _N) * (1 + _N2 / 4 + _N4 / 64 + _N6 / 256)
_ALPHA = (
    _N / 2 - 2 * _N2 / 3 + 5 * _N3 / 16 + 41 * _N4 / 180 - 127 * _N5 / 288 + 7891 * _N6 / 37800,
    13 * _N2 / 48 - 3 * _N3 / 5 + 557 * _N4 / 1440 + 281 * _N5 / 630 - 1983433 * _N6 / 1935360,
    61 * _N3 / 240 - 103 * _N4 / 140 + 15061 * _N5 / 26880 + 167603 * _N6 / 181440,
    49561 * _N4 / 161280 - 179 * _N5 / 168 + 6601661 * _N6 / 7257600,
    34729 * _N5 / 80640 - 3418889 * _N6 / 1995840,
    212378941 * _N6 / 319334400,
)
_BETA = (
    _N / 2 - 2 * _N2 / 3 + 37 * _N3 / 96 - _N4 / 360 - 81 * _N5 / 512 + 96199 * _N6 / 604800,
    _N2 / 48 + _N3 / 15 - 437 * _N4 / 1440 + 46 * _N5 / 105 - 1118711 * _N6 / 3870720,
    17 * _N3 / 480 - 37 * _N4 / 840 - 209 * _N5 / 4480 + 5569 * _N6 / 90720,
    4397 * _N4 / 161280 - 11 * _N5 / 504 - 830251 * _N6 / 7257600,
    4583 * _N5 / 161280 - 108847 * _N6 / 3991680,
    20648693 * _N6 / 638668800,
)
_E = math.sqrt(_F * (2 - _F))


def fwd(lon, lat):
    phi, lam = math.radians(lat), math.radians(lon) - _LON0
    t = math.sinh(math.atanh(math.sin(phi)) - _E * math.atanh(_E * math.sin(phi)))
    xi_ = math.atan2(t, math.cos(lam))
    eta_ = math.atanh(math.sin(lam) / math.sqrt(1 + t * t))
    xi, eta = xi_, eta_
    for j, a in enumerate(_ALPHA, 1):
        xi += a * math.sin(2 * j * xi_) * math.cosh(2 * j * eta_)
        eta += a * math.cos(2 * j * xi_) * math.sinh(2 * j * eta_)
    return _FE + _K0 * _AA * eta, _FN + _K0 * _AA * xi


def inv(x, y):
    xi = (y - _FN) / (_K0 * _AA)
    eta = (x - _FE) / (_K0 * _AA)
    xi_, eta_ = xi, eta
    for j, b in enumerate(_BETA, 1):
        xi_ -= b * math.sin(2 * j * xi) * math.cosh(2 * j * eta)
        eta_ -= b * math.cos(2 * j * xi) * math.sinh(2 * j * eta)
    tau_ = math.sin(xi_) / math.sqrt(math.sinh(eta_) ** 2 + math.cos(xi_) ** 2)
    lam = math.atan2(math.sinh(eta_), math.cos(xi_))
    tau = tau_
    for _ in range(6):   # Newton (Karney)
        s = math.sinh(_E * math.atanh(_E * tau / math.sqrt(1 + tau * tau)))
        ti_ = tau * math.sqrt(1 + s * s) - s * math.sqrt(1 + tau * tau)
        d = (tau_ - ti_) / math.sqrt(1 + ti_ * ti_) * (1 + (1 - _E * _E) * tau * tau) / \
            ((1 - _E * _E) * math.sqrt(1 + tau * tau))
        tau += d
        if abs(d) < 1e-14:
            break
    return math.degrees(lam + _LON0), math.degrees(math.atan(tau))


class Transformer:
    def __init__(self, f):
        self._f = f

    @classmethod
    def from_crs(cls, a, b, always_xy=True):
        a, b = str(a).upper(), str(b).upper()
        if (a, b) == ("EPSG:4326", "EPSG:31983"):
            return cls(fwd)
        if (a, b) == ("EPSG:31983", "EPSG:4326"):
            return cls(inv)
        raise NotImplementedError(f"compat pyproj: {a}->{b}")

    def transform(self, x, y):
        return self._f(x, y)
