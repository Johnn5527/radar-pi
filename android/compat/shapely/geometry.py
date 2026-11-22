import math


class _Geom:
    @property
    def centroid(self):
        x0, y0, x1, y1 = self.bounds
        return Point((x0 + x1) / 2, (y0 + y1) / 2)


class Point(_Geom):
    __slots__ = ("x", "y")

    def __init__(self, x, y=None):
        if y is None:
            x, y = x
        self.x, self.y = float(x), float(y)

    @property
    def coords(self):
        return [(self.x, self.y)]

    @property
    def bounds(self):
        return (self.x, self.y, self.x, self.y)

    def buffer(self, r):
        return _Box(self.x - r, self.y - r, self.x + r, self.y + r)

    def distance(self, o):
        if isinstance(o, Point):
            return math.hypot(self.x - o.x, self.y - o.y)
        return o.distance(self)


class _Box(_Geom):
    __slots__ = ("bounds",)

    def __init__(self, x0, y0, x1, y1):
        self.bounds = (x0, y0, x1, y1)


def box(x0, y0, x1, y1):
    return _Box(min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))


class LineString(_Geom):
    __slots__ = ("_c", "_cum", "bounds", "length")

    def __init__(self, coords):
        c = [(float(p[0]), float(p[1])) for p in coords]
        self._c = c
        cum = [0.0]
        for (x0, y0), (x1, y1) in zip(c, c[1:]):
            cum.append(cum[-1] + math.hypot(x1 - x0, y1 - y0))
        self._cum = cum
        self.length = cum[-1]
        xs = [p[0] for p in c]
        ys = [p[1] for p in c]
        self.bounds = (min(xs), min(ys), max(xs), max(ys))

    @property
    def coords(self):
        return list(self._c)

    def _proj(self, px, py):
        best = (math.inf, 0.0)
        c, cum = self._c, self._cum
        for k in range(len(c) - 1):
            x0, y0 = c[k]
            x1, y1 = c[k + 1]
            dx, dy = x1 - x0, y1 - y0
            L2 = dx * dx + dy * dy
            t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((px - x0) * dx + (py - y0) * dy) / L2))
            qx, qy = x0 + t * dx, y0 + t * dy
            d = math.hypot(px - qx, py - qy)
            if d < best[0]:
                best = (d, cum[k] + t * math.sqrt(L2))
        return best

    def distance(self, p):
        return self._proj(p.x, p.y)[0]

    def project(self, p):
        return self._proj(p.x, p.y)[1]

    def interpolate(self, d):
        c, cum = self._c, self._cum
        if d < 0:          # igual ao shapely: distância negativa conta a partir do fim
            d += self.length
        d = max(0.0, min(self.length, d))
        if len(c) == 1:
            return Point(*c[0])
        lo, hi = 0, len(cum) - 1
        while hi - lo > 1:
            mid = (lo + hi) // 2
            if cum[mid] <= d:
                lo = mid
            else:
                hi = mid
        seg = cum[hi] - cum[lo]
        t = 0.0 if seg == 0 else (d - cum[lo]) / seg
        (x0, y0), (x1, y1) = c[lo], c[hi]
        return Point(x0 + t * (x1 - x0), y0 + t * (y1 - y0))
