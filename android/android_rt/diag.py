"""Diagnóstico à prova de crash silencioso (sem PC, sem logcat).

- log(): grava cada passo em  Download/radar-pi-log.txt  (MediaStore, Android 10+; abre no app Arquivos)
  e também em Android/data/<pacote>/files/radar-pi.log e no logcat.
- faulthandler escreve no MESMO arquivo do Download: se o Python morrer por crash nativo (segfault em
  pygame/numpy/SDL), o traceback fica lá.
- mostrar_erro(): em vez de fechar, mostra o traceback na tela (pygame; se nem o pygame subir, caixa
  de diálogo nativa do Android) e fica aberto até o usuário apertar Voltar.
Só depende de os/sys/time/faulthandler e, no Android, de pyjnius."""
from __future__ import annotations

import faulthandler
import os
import sys
import time
import traceback

NO_ANDROID = "ANDROID_ARGUMENT" in os.environ or "ANDROID_PRIVATE" in os.environ
NOME_LOG = "radar-pi-log.txt"
_fd = None
_ext = None
_onde = []


def _activity():
    from jnius import autoclass
    return autoclass("org.kivy.android.PythonActivity").mActivity


def _abrir_download():
    """fd gravável de Download/radar-pi-log.txt (reaproveita o arquivo se já for deste app)."""
    from jnius import autoclass
    act = _activity()
    res = act.getContentResolver()
    MC = autoclass("android.provider.MediaStore$MediaColumns")
    DL = autoclass("android.provider.MediaStore$Downloads")
    base = DL.EXTERNAL_CONTENT_URI
    uri = None
    try:
        cur = res.query(base, [MC._ID], MC.DISPLAY_NAME + "=?", [NOME_LOG], None)
        if cur is not None:
            if cur.moveToFirst():
                CU = autoclass("android.content.ContentUris")
                uri = CU.withAppendedId(base, cur.getLong(0))
            cur.close()
    except Exception:  # noqa: BLE001
        uri = None
    if uri is None:
        CV = autoclass("android.content.ContentValues")
        cv = CV()
        cv.put(MC.DISPLAY_NAME, NOME_LOG)
        cv.put(MC.MIME_TYPE, "text/plain")
        cv.put(MC.RELATIVE_PATH, "Download/")
        uri = res.insert(base, cv)
    pfd = res.openFileDescriptor(uri, "rwt")
    return pfd.detachFd()


def iniciar():
    """Abre os logs e liga o faulthandler. Nunca levanta exceção."""
    global _fd, _ext
    if NO_ANDROID:
        try:
            _fd = _abrir_download()
            _onde.append("Download/" + NOME_LOG)
        except Exception as e:  # noqa: BLE001
            print("[radar-pi] sem log no Download:", e)
        try:
            f = _activity().getExternalFilesDir(None)
            _ext = f.getAbsolutePath() if f else None
            if _ext:
                _onde.append(_ext + "/radar-pi.log")
        except Exception:  # noqa: BLE001
            pass
    try:
        if _fd is not None:
            faulthandler.enable(file=_fd, all_threads=True)
        else:
            faulthandler.enable(all_threads=True)
    except Exception:  # noqa: BLE001
        pass
    log("radar-pi iniciando; python", sys.version.split()[0], "| android:", NO_ANDROID, "| logs:", _onde)


def log(*a):
    msg = time.strftime("%Y-%m-%d %H:%M:%S ") + " ".join(str(x) for x in a)
    print("[radar-pi]", msg, flush=True)
    b = (msg + "\n").encode("utf-8", "replace")
    if _fd is not None:
        try:
            os.write(_fd, b)
        except Exception:  # noqa: BLE001
            pass
    if _ext:
        try:
            with open(os.path.join(_ext, "radar-pi.log"), "ab") as f:
                f.write(b)
        except Exception:  # noqa: BLE001
            pass


def esconder_loading():
    if NO_ANDROID:
        try:
            from android import loadingscreen
            loadingscreen.hide_loading_screen()
        except Exception as e:  # noqa: BLE001
            log("loadingscreen:", e)


def _erro_pygame(texto, fonte_path=None):
    import pygame
    if not pygame.get_init():
        pygame.init()
    tela = pygame.display.get_surface()
    if tela is None:
        try:
            tela = pygame.display.set_mode((960, 540), pygame.FULLSCREEN | pygame.SCALED)
        except Exception:  # noqa: BLE001
            tela = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
    W, H = tela.get_size()
    try:
        f = pygame.font.Font(fonte_path, max(12, H // 34)) if fonte_path else pygame.font.Font(None, max(14, H // 30))
    except Exception:  # noqa: BLE001
        f = pygame.font.Font(None, max(14, H // 30))
    linhas = []
    largura = max(20, int(W / (f.size("M")[0] or 8)))
    for ln in ("radar-pi: erro ao iniciar (Voltar fecha). Log: " + ", ".join(_onde) + "\n\n" + texto).splitlines():
        while len(ln) > largura:
            linhas.append(ln[:largura])
            ln = "  " + ln[largura:]
        linhas.append(ln)
    linhas = linhas[-int(H / f.get_linesize()):]
    esconder_loading()
    rel = pygame.time.Clock()
    while True:
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT or (ev.type == pygame.KEYDOWN and ev.key in (pygame.K_AC_BACK, pygame.K_ESCAPE)):
                return
        tela.fill((255, 255, 255))
        y = 4
        for ln in linhas:
            tela.blit(f.render(ln, True, (160, 0, 0) if y == 4 else (20, 20, 20)), (6, y))
            y += f.get_linesize()
        pygame.display.flip()
        rel.tick(5)


def _erro_android(texto):
    from android.runnable import run_on_ui_thread
    from jnius import autoclass, cast
    String = autoclass("java.lang.String")
    Builder = autoclass("android.app.AlertDialog$Builder")

    @run_on_ui_thread
    def _f():
        b = Builder(_activity())
        b.setTitle(cast("java.lang.CharSequence", String("radar-pi: erro ao iniciar")))
        b.setMessage(cast("java.lang.CharSequence", String("Log: " + ", ".join(_onde) + "\n\n" + texto[-3500:])))
        b.setCancelable(False)
        b.show()
    esconder_loading()
    _f()
    while True:          # não deixa o processo morrer: o diálogo fica na tela
        time.sleep(1)


def mostrar_erro(texto, fonte_path=None):
    log("ERRO:\n" + texto)
    try:
        _erro_pygame(texto, fonte_path)
        return
    except Exception:  # noqa: BLE001
        log("tela de erro (pygame) falhou:\n" + traceback.format_exc())
    if NO_ANDROID:
        try:
            _erro_android(texto)
        except Exception:  # noqa: BLE001
            log("diálogo de erro (Android) falhou:\n" + traceback.format_exc())
            while True:
                time.sleep(1)
