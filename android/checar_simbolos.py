"""Confere, num APK, se todo símbolo indefinido de cada .so (libs + bundle Python + private) é exportado por
outra .so empacotada, pela libpython ou pelas libs do sistema Android (NDK sysroot, mesmo nível de API).
Uso: python android/checar_simbolos.py app.apk  -> lista o que ficaria sem resolver (exit 1 se houver)."""
import glob
import os
import subprocess
import sys
import tarfile
import tempfile
import zipfile

NDK = [d for d in glob.glob(os.path.expanduser("~/.buildozer/android/platform/android-ndk-*")) if os.path.isdir(d)][0]
BIN = f"{NDK}/toolchains/llvm/prebuilt/linux-x86_64/bin"
API = 26
SYSROOT = f"{NDK}/toolchains/llvm/prebuilt/linux-x86_64/sysroot/usr/lib/aarch64-linux-android"
SISTEMA = ["libc.so", "libm.so", "libdl.so", "liblog.so", "libandroid.so", "libz.so", "libGLESv1_CM.so",
           "libGLESv2.so", "libGLESv3.so", "libEGL.so", "libOpenSLES.so", "libjnigraphics.so", "libaaudio.so"]


def simbolos(path, indefinidos):
    args = [f"{BIN}/llvm-nm", "-D", "--undefined-only" if indefinidos else "--defined-only", path]
    out = subprocess.run(args, capture_output=True, text=True).stdout
    s = set()
    for ln in out.splitlines():
        p = ln.split()
        if not p:
            continue
        if indefinidos and p[0] == "w":       # fraco: pode faltar
            continue
        s.add(p[-1].split("@")[0])
    return s


def main(apk):
    tmp = tempfile.mkdtemp()
    with zipfile.ZipFile(apk) as z:
        z.extractall(tmp)
    for t, d in ((f"{tmp}/lib/arm64-v8a/libpybundle.so", "bundle"), (f"{tmp}/assets/private.tar", "private")):
        if os.path.exists(t):
            with tarfile.open(t) as tf:
                tf.extractall(f"{tmp}/{d}", filter="fully_trusted")
    subprocess.run(["chmod", "-R", "u+rwX", tmp])
    sos = [p for p in glob.glob(f"{tmp}/**/*.so", recursive=True)]
    exportados = set()
    for p in sos:
        exportados |= simbolos(p, False)
    for n in SISTEMA:
        f = f"{SYSROOT}/{API}/{n}"
        if os.path.exists(f):
            exportados |= simbolos(f, False)
    libcxx = f"{SYSROOT}/libc++_shared.so"
    exportados |= simbolos(libcxx, False) if os.path.exists(libcxx) else set()
    falta = {}
    for p in sos:
        for s in simbolos(p, True) - exportados:
            falta.setdefault(os.path.relpath(p, tmp), []).append(s)
    print(f"{len(sos)} .so conferidas; {len(exportados)} símbolos exportados disponíveis")
    for k, v in sorted(falta.items()):
        print("SEM RESOLVER", k, sorted(v)[:12], f"({len(v)})")
    return 1 if falta else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
