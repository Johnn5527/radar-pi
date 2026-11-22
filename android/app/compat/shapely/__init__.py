"""Substituto mínimo do shapely, SÓ para o APK Android (GEOS/shapely 2 não compilam no python-for-android).
Cobre o que o radar-pi usa em tempo de execução: Point, LineString, box, STRtree (consulta por bbox,
igual ao STRtree.query sem predicado). Validado contra o shapely real em android/tests/test_compat.py."""
from .geometry import LineString, Point, box  # noqa: F401
from .strtree import STRtree  # noqa: F401
