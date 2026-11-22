"""Tela simulada 320x240 (estilo display SPI 3,5" do Pi), renderizada em PNG com Pillow."""
from __future__ import annotations

from PIL import Image, ImageDraw, ImageFont

W, H = 320, 240
_F = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


def _fonte(sz):
    try:
        return ImageFont.truetype(_F, sz)
    except OSError:
        return ImageFont.load_default()


def render(alerta=None, velocidade_kmh=0.0, via="", sem_gps=False, via_incerta=False, caminho=None):
    img = Image.new("RGB", (W, H), (12, 14, 18))
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W, 24], fill=(30, 34, 42))
    d.text((6, 4), (via or "—")[:30], font=_fonte(13), fill=(220, 220, 220))
    d.text((W - 70, 4), "SEM GPS" if sem_gps else "GPS OK", font=_fonte(12),
           fill=(255, 170, 0) if sem_gps else (90, 200, 120))
    if alerta is None:
        d.text((W // 2, 105), f"{velocidade_kmh:.0f}", font=_fonte(72), fill=(235, 235, 235), anchor="mm")
        d.text((W // 2, 160), "km/h", font=_fonte(18), fill=(150, 150, 150), anchor="mm")
        d.text((W // 2, 215), "Nenhum radar à frente", font=_fonte(14), fill=(120, 120, 120), anchor="mm")
    else:
        d.rectangle([0, 24, W, 62], fill=(170, 20, 20) if not via_incerta else (170, 110, 0))
        titulo = f"RADAR A {alerta.distancia_m:.0f} m" if not via_incerta else f"RADAR PRÓXIMO ~{alerta.distancia_m:.0f} m (via incerta)"
        d.text((W // 2, 43), titulo, font=_fonte(19 if not via_incerta else 13), fill="white", anchor="mm")
        # placa de limite
        cx, cy, r = 80, 145, 58
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill="white", outline=(200, 0, 0), width=11)
        lt = alerta.limite_texto
        d.text((cx, cy), lt, font=_fonte(40 if len(lt) <= 3 else 22), fill="black", anchor="mm")
        if alerta.limite_estimado:
            d.text((cx, cy + r + 12), "limite estimado (OSM)", font=_fonte(10), fill=(255, 200, 0), anchor="mm")
        # velocidade
        cor = (255, 60, 60) if alerta.acima else (90, 220, 120)
        d.text((235, 130), f"{alerta.velocidade_kmh:.0f}", font=_fonte(64), fill=cor, anchor="mm")
        d.text((235, 180), "km/h", font=_fonte(16), fill=(170, 170, 170), anchor="mm")
        if alerta.acima:
            d.text((235, 215), "REDUZA!", font=_fonte(20), fill=(255, 60, 60), anchor="mm")
    if caminho:
        img.save(caminho)
    return img
