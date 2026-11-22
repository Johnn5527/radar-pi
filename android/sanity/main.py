"""APK de sanidade: mesmo bootstrap/receitas do radar-pi. Mostra 'ok' + o resultado de cada import."""
import os
import sys
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from android_rt import diag  # noqa: E402

diag.iniciar()
res = ["python " + sys.version.split()[0]]
for nome in ("pygame", "numpy", "sqlite3", "jnius", "android.permissions"):
    try:
        m = __import__(nome)
        v = getattr(getattr(m, "version", None), "ver", None) or getattr(m, "__version__", "") or getattr(m, "sqlite_version", "")
        res.append(f"{nome}: ok {v}")
    except BaseException:  # noqa: BLE001
        res.append(f"{nome}: FALHOU {traceback.format_exc(limit=1).strip().splitlines()[-1]}")
    diag.log(res[-1])
try:
    import pygame
    pygame.init()
    tela = pygame.display.set_mode((960, 540), pygame.FULLSCREEN | pygame.SCALED)
    f1, f2 = pygame.font.Font(None, 160), pygame.font.Font(None, 34)
    diag.esconder_loading()
    diag.log("tela ok")
    while True:
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT or (ev.type == pygame.KEYDOWN and ev.key == pygame.K_AC_BACK):
                raise SystemExit
        tela.fill((255, 255, 255))
        tela.blit(f1.render("ok", True, (0, 130, 0)), (40, 20))
        for k, ln in enumerate(res):
            tela.blit(f2.render(ln, True, (20, 20, 20)), (40, 190 + 36 * k))
        pygame.display.flip()
        time.sleep(0.1)
except SystemExit:
    pass
except BaseException:  # noqa: BLE001
    diag.mostrar_erro("\n".join(res) + "\n\n" + traceback.format_exc())
