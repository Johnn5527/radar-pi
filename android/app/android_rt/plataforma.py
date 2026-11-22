"""Pontes com o Android via pyjnius (só no APK). Fora do Android tudo vira no-op."""
from __future__ import annotations

import os
import threading
import time

NO_ANDROID = "ANDROID_ARGUMENT" in os.environ or "ANDROID_PRIVATE" in os.environ


def _activity():
    from jnius import autoclass
    return autoclass("org.kivy.android.PythonActivity").mActivity


def pasta_externa():
    """/storage/emulated/0/Android/data/<pacote>/files (onde vai o radar.env opcional e os logs)."""
    if not NO_ANDROID:
        return None
    try:
        f = _activity().getExternalFilesDir(None)
        return f.getAbsolutePath() if f else None
    except Exception:  # noqa: BLE001
        return None


def extras_intent():
    """Extras string do intent (adb shell am start ... --es RADAR_GPS sim)."""
    out = {}
    if not NO_ANDROID:
        return out
    try:
        it = _activity().getIntent()
        b = it.getExtras() if it else None
        if b:
            for k in b.keySet().toArray():
                if str(k).startswith("RADAR_"):
                    v = b.get(k)
                    if v is not None:
                        out[str(k)] = str(v.toString())
    except Exception:  # noqa: BLE001
        pass
    return out


def pedir_permissao_localizacao():
    if not NO_ANDROID:
        return
    from android.permissions import Permission, request_permissions

    def _resp(perms, ok):
        _log("resposta da permissão:", list(zip([str(p) for p in perms], list(ok))))
    request_permissions([Permission.ACCESS_FINE_LOCATION, Permission.ACCESS_COARSE_LOCATION], _resp)


def tem_permissao_localizacao():
    if not NO_ANDROID:
        return False
    from android.permissions import Permission, check_permission
    return check_permission(Permission.ACCESS_FINE_LOCATION)


def manter_tela_ligada():
    if not NO_ANDROID:
        return
    from android.runnable import run_on_ui_thread
    from jnius import autoclass
    LP = autoclass("android.view.WindowManager$LayoutParams")

    @run_on_ui_thread
    def _f():
        _activity().getWindow().addFlags(LP.FLAG_KEEP_SCREEN_ON)
    _f()


def tamanho_tela():
    """Resolução física (px) da tela, para escolher a proporção da superfície lógica."""
    from jnius import autoclass
    DM = autoclass("android.util.DisplayMetrics")
    dm = DM()
    _activity().getWindowManager().getDefaultDisplay().getRealMetrics(dm)
    return dm.widthPixels, dm.heightPixels


# ---------------------------------------------------------------- voz (TTS do sistema, fallback)
class TTSAndroid:
    """android.speech.tts.TextToSpeech em pt-BR (offline se o pacote de voz pt-BR estiver no aparelho)."""

    def __init__(self):
        self.ok = False
        self.tts = None
        if not NO_ANDROID:
            return
        from jnius import PythonJavaClass, autoclass, java_method

        dono = self

        class _Init(PythonJavaClass):
            __javainterfaces__ = ["android/speech/tts/TextToSpeech$OnInitListener"]
            __javacontext__ = "app"

            @java_method("(I)V")
            def onInit(self, status):
                dono.ok = status == 0
                if dono.ok:
                    Locale = autoclass("java.util.Locale")
                    dono.tts.setLanguage(Locale("pt", "BR"))

        self._init = _Init()
        TTS = autoclass("android.speech.tts.TextToSpeech")
        self._cls = TTS
        self.tts = TTS(_activity(), self._init)

    def falar(self, texto):
        if not (self.ok and self.tts):
            return False
        self.tts.speak(texto, self._cls.QUEUE_FLUSH, None, "radar")
        return True


# ---------------------------------------------------------------- GPS do aparelho
def _log(*a):
    try:
        from android_rt import diag
        diag.log(*a)
    except Exception:  # noqa: BLE001
        print("[radar-pi]", *a)


class FiltroVelocidade:
    """Velocidade confiável: Location.getSpeed só com hasSpeed e precisão boa; senão derivada da posição
    (filtrada); 0 quando parado; descarta valores absurdos (>200 km/h) e fixes ruins."""

    ACC_MAX_FIX = 100.0     # m: pior que isso o fix é descartado
    ACC_MAX_VEL = 30.0      # m: precisão mínima para confiar no getSpeed
    VMAX = 55.0             # m/s (~200 km/h)

    def __init__(self):
        self.hist = []      # [(t, lat, lon)] dos últimos 4 s
        self.v = 0.0

    @staticmethod
    def _dist(la1, lo1, la2, lo2):
        import math
        dy = (la2 - la1) * 111320.0
        dx = (lo2 - lo1) * 111320.0 * math.cos(math.radians((la1 + la2) / 2))
        return math.hypot(dx, dy)

    def aplicar(self, t, lat, lon, acc, tem_vel, vel_bruta):
        """Devolve (aceito, vel_ms)."""
        if acc is not None and acc > self.ACC_MAX_FIX:
            return False, self.v
        hist = self.hist = [h for h in getattr(self, "hist", []) if t - h[0] <= 4.0] + [(t, lat, lon)]
        derivada = None
        if len(hist) >= 2 and hist[-1][0] - hist[0][0] >= 1.5:
            dt = hist[-1][0] - hist[0][0]
            d = self._dist(hist[0][1], hist[0][2], lat, lon)
            # deslocamento menor que o ruído (2,5x a precisão, mín. 15 m) = parado
            derivada = d / dt if d > max(15.0, 2.5 * (acc or 10.0)) else 0.0
        if tem_vel and vel_bruta is not None and 0 <= vel_bruta <= self.VMAX and (acc or 10.0) <= self.ACC_MAX_VEL:
            v = vel_bruta
        elif derivada is not None and derivada <= self.VMAX:
            v = 0.5 * self.v + 0.5 * derivada
        else:
            v = self.v
        if v < 0.8:          # parado (ruído do GPS)
            v = 0.0
        self.v = v
        return True, v


class GPSAndroid:
    """LocationManager: GPS + NETWORK (e FUSED se existir), minTime 500 ms, callbacks no Looper principal.
    Só pede atualizações depois da permissão concedida; tenta de novo a cada 5 s se não subir; posição
    inicial rápida por getLastKnownLocation. Cada fix vai para o log de diagnóstico."""

    PROVEDORES = ("gps", "fused", "network")

    def __init__(self):
        self.fila = []
        self.lock = threading.Lock()
        self.t0 = time.monotonic()
        self.ativo = False
        self.ultimo = 0.0
        self.erro = None
        self.provedores = []
        self._t_pedido = 0.0
        self.filtro = FiltroVelocidade()
        self.n_fix = 0
        self.cobertura = None      # função (lat, lon) -> bool, posta pelo main
        self.ult_gps = 0.0
        if not NO_ANDROID:
            return
        from jnius import PythonJavaClass, autoclass, java_method

        dono = self

        class _Ouvinte(PythonJavaClass):
            __javainterfaces__ = ["android/location/LocationListener"]
            __javacontext__ = "app"

            @java_method("(Landroid/location/Location;)V")
            def onLocationChanged(self, loc):
                dono._novo(loc)

            @java_method("(Ljava/util/List;)V", name="onLocationChanged")
            def onLocationChangedLista(self, lista):
                for k in range(lista.size()):
                    dono._novo(lista.get(k))

            @java_method("(Ljava/lang/String;)V")
            def onProviderEnabled(self, p):
                _log("provedor ligado:", p)

            @java_method("(Ljava/lang/String;)V")
            def onProviderDisabled(self, p):
                _log("provedor DESLIGADO:", p)

            @java_method("(Ljava/lang/String;ILandroid/os/Bundle;)V")
            def onStatusChanged(self, p, s, b):
                pass

            @java_method("(I)V")
            def onFlushComplete(self, c):
                pass

        self._ouvinte = _Ouvinte()          # referência forte: o proxy Java não pode ser coletado
        self._Ctx = autoclass("android.content.Context")
        self._Looper = autoclass("android.os.Looper")

    def iniciar(self):
        """Idempotente; chamar a cada quadro: só age a cada 5 s enquanto não estiver ativo."""
        if not NO_ANDROID or self.ativo:
            return
        agora = time.monotonic()
        if agora - self._t_pedido < 5.0:
            return
        self._t_pedido = agora
        from android.runnable import run_on_ui_thread
        try:
            lm = _activity().getSystemService(self._Ctx.LOCATION_SERVICE)
            self._lm = lm
        except Exception as e:  # noqa: BLE001
            self.erro = f"LocationManager: {e}"
            _log("GPS erro:", self.erro)
            return

        @run_on_ui_thread
        def _f():
            import traceback
            try:
                lm = self._lm
                todos = [str(p) for p in lm.getAllProviders().toArray()]
                ligados = [str(p) for p in lm.getProviders(True).toArray()]
                _log("provedores:", todos, "ligados:", ligados, "| localização do aparelho ligada:",
                     _seguro_bool(lambda: lm.isLocationEnabled()))
                ok = []
                for p in self.PROVEDORES:
                    if p not in todos:
                        continue
                    try:
                        lm.requestLocationUpdates(p, 500, 0.0, self._ouvinte, self._Looper.getMainLooper())
                        ok.append(p)
                    except Exception as e:  # noqa: BLE001
                        _log(f"requestLocationUpdates({p}) falhou: {type(e).__name__}: {e}")
                    try:
                        loc = lm.getLastKnownLocation(p)
                        if loc is not None:
                            idade = (_agora_ms() - loc.getTime()) / 1000.0
                            _log(f"última posição conhecida ({p}): idade {idade:.0f}s")
                            if idade < 120:
                                self._novo(loc, inicial=True)
                    except Exception as e:  # noqa: BLE001
                        _log(f"getLastKnownLocation({p}) falhou: {e}")
                self.provedores = ok
                self.ativo = bool(ok)
                self.erro = None if ok else "nenhum provedor aceitou o pedido"
                _log("GPS pedido; ativo:", self.ativo, "provedores:", ok, "erro:", self.erro)
            except Exception as e:  # noqa: BLE001
                self.erro = f"{type(e).__name__}: {e}"
                _log("GPS falhou ao iniciar:\n" + traceback.format_exc())
        _f()

    def parar(self):
        if self.ativo:
            try:
                self._lm.removeUpdates(self._ouvinte)
            except Exception:  # noqa: BLE001
                pass
            self.ativo = False
            self._t_pedido = 0.0

    def _novo(self, loc, inicial=False):
        try:
            from radar_pi.mapmatch.hmm import Fix
            prov = str(loc.getProvider())
            t = time.monotonic() - self.t0
            # se o GPS está mandando fixes, ignora os de rede (piores) por 5 s
            if prov == "gps":
                self.ult_gps = t
            elif t - self.ult_gps < 5.0:
                return
            lat, lon = float(loc.getLatitude()), float(loc.getLongitude())
            acc = float(loc.getAccuracy()) if loc.hasAccuracy() else None
            tem_vel = bool(loc.hasSpeed())
            bruta = float(loc.getSpeed()) if tem_vel else None
            aceito, vel = self.filtro.aplicar(t, lat, lon, acc, tem_vel, bruta)
            rumo = float(loc.getBearing()) if loc.hasBearing() and vel > 1.5 else None
            dentro = None
            if self.cobertura:
                try:
                    dentro = self.cobertura(lat, lon)
                except Exception:  # noqa: BLE001
                    dentro = None
            self.n_fix += 1
            _log(f"fix#{self.n_fix} {prov}{' (inicial)' if inicial else ''} lat={lat:.3f} lon={lon:.3f} "
                 f"acc={acc if acc is None else round(acc)} speed_bruto={bruta} -> vel={vel:.1f}m/s "
                 f"rumo={rumo if rumo is None else round(rumo)} aceito={aceito} dentro_mapa={dentro}")
            if not aceito:
                return
            f = Fix(t, lat, lon, vel, rumo, acc if acc is not None else 30.0)
            with self.lock:
                self.fila.append(f)
                self.ultimo = time.monotonic()
        except Exception as e:  # noqa: BLE001
            _log("erro ao ler Location:", type(e).__name__, e)

    def novos(self, t_sim=0.0):
        from radar_pi.mapmatch.hmm import Fix
        with self.lock:
            out, self.fila = self.fila, []
        # sem fix há >3 s (túnel/garagem): entrega "sem GPS" 1x/s, como o gpsd faz no Pi
        if not out and self.ultimo and time.monotonic() - self.ultimo > 3.0:
            agora = time.monotonic()
            if agora - getattr(self, "_ult_vazio", 0) >= 1.0:
                self._ult_vazio = agora
                out = [Fix(agora - self.t0, None, None, 0.0, None, 99.0)]
        return out

    fim = False


def _agora_ms():
    return time.time() * 1000.0


def _seguro_bool(f):
    try:
        return bool(f())
    except Exception:  # noqa: BLE001
        return None
