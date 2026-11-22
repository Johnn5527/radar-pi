import math

_CEL = 250.0


class STRtree:
    """Índice por grade uniforme (250 m). query(geom) devolve os índices cujas caixas tocam a caixa de geom."""

    def __init__(self, geoms):
        self.geoms = list(geoms)
        self.bbs = [g.bounds for g in self.geoms]
        self.grade = {}
        for i, (x0, y0, x1, y1) in enumerate(self.bbs):
            for gx in range(math.floor(x0 / _CEL), math.floor(x1 / _CEL) + 1):
                for gy in range(math.floor(y0 / _CEL), math.floor(y1 / _CEL) + 1):
                    self.grade.setdefault((gx, gy), []).append(i)

    def __len__(self):
        return len(self.geoms)

    def query(self, geom):
        x0, y0, x1, y1 = geom.bounds
        vist = set()
        out = []
        bbs = self.bbs
        for gx in range(math.floor(x0 / _CEL), math.floor(x1 / _CEL) + 1):
            for gy in range(math.floor(y0 / _CEL), math.floor(y1 / _CEL) + 1):
                for i in self.grade.get((gx, gy), ()):
                    if i in vist:
                        continue
                    vist.add(i)
                    a = bbs[i]
                    if a[0] <= x1 and a[2] >= x0 and a[1] <= y1 and a[3] >= y0:
                        out.append(i)
        return out
