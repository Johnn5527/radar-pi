"""pygame 2.6.1 para python-for-android (a receita oficial traz 2.1.0, que não compila no CPython 3.14).
Inclui os blitters SIMD (NEON via sse2neon) que o template Android esquece. Tira o _sprite (Cython experimental que o Cython novo recusa; pygame.sprite puro continua)."""
import os
from os.path import join

from pythonforandroid.recipes.pygame import Pygame2Recipe


class Pygame261Recipe(Pygame2Recipe):
    version = '2.6.1'
    url = 'https://github.com/pygame/pygame/archive/{version}.tar.gz'
    hostpython_prerequisites = ['setuptools', 'cython>=3.0']

    def prebuild_arch(self, arch):
        d = self.get_build_dir(arch.arch)
        for f in ("src_c/cython/pygame/_sprite.pyx",):
            if os.path.exists(join(d, f)):
                os.remove(join(d, f))
        t = join(d, "buildconfig", "Setup.Android.SDL2.in")
        s = open(t).read()
        s = "\n".join(x for x in s.splitlines() if not x.startswith("_sprite "))
        # O template Android do pygame esquece os blitters SIMD: no aarch64 o alphablit.c liga o caminho
        # NEON (sse2neon) e referencia alphablit_*_sse2_* / *_avx2_*, que ficam indefinidos no surface.so
        # ("cannot locate symbol alphablit_alpha_sse2_argb_surf_alpha"). Compila os mesmos arquivos do Setup desktop.
        s = s.replace("surface src_c/surface.c", "surface src_c/simd_blitters_sse2.c src_c/simd_blitters_avx2.c src_c/surface.c")
        open(t, "w").write(s + "\n")
        super().prebuild_arch(arch)


recipe = Pygame261Recipe()
