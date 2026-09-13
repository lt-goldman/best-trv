"""One-off script to generate the Best TRV brand icon assets.

Not part of the integration itself - just used to produce
custom_components/best_trv/brand/{icon,icon@2x,logo,logo@2x}.png.
Renders at a high-res master and downsamples for clean anti-aliasing.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

MASTER = 2048
OUT_DIR = Path(__file__).resolve().parents[1] / "custom_components" / "best_trv" / "brand"

WARM = np.array([255, 87, 51])    # deep orange-red (heat)
COOL = np.array([37, 130, 255])   # vivid blue (cool)
WHITE = (255, 255, 255, 255)
SHADOW = (10, 20, 40, 130)


def squircle_mask(size: int, radius: float) -> Image.Image:
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill=255)
    return mask


def gradient_layer(size: int, mask: Image.Image) -> Image.Image:
    y, x = np.mgrid[0:size, 0:size]
    # diagonal 0..1, bottom-left (warm) -> top-right (cool)
    t = ((x + (size - y)) / (2 * size))
    t = np.clip(t, 0, 1)[..., None]
    color = (WARM[None, None, :] * (1 - t) + COOL[None, None, :] * t).astype(np.uint8)
    alpha = np.array(mask)[..., None]
    return Image.fromarray(np.dstack([color, alpha]), mode="RGBA")


def glass_highlight(size: int, mask: Image.Image) -> Image.Image:
    """A soft radial sheen in the upper-left, like light on glass/enamel."""
    y, x = np.mgrid[0:size, 0:size].astype(np.float64)
    cx, cy = size * 0.34, size * 0.26
    r = size * 0.62
    dist = np.sqrt((x - cx) ** 2 + (y - cy) ** 2) / r
    alpha = np.clip(1 - dist, 0, 1) ** 2.2 * 150
    alpha = alpha.astype(np.uint8)[..., None]
    white = np.full((size, size, 3), 255, dtype=np.uint8)
    layer = Image.fromarray(np.dstack([white, alpha]), mode="RGBA")
    layer.putalpha(Image.composite(layer.split()[3], Image.new("L", (size, size), 0), mask))
    return layer


def rounded_bar(draw: ImageDraw.ImageDraw, box: list[float], fill) -> None:
    """A stadium/pill shape: radius follows the SHORTER side, whichever
    orientation the bar has, so both tall fins and wide pipes look right."""
    x0, y0, x1, y1 = box
    radius = min(x1 - x0, y1 - y0) / 2
    draw.rounded_rectangle([x0, y0, x1, y1], radius=radius, fill=fill)


def radiator_glyph(size: int) -> tuple[Image.Image, Image.Image]:
    """Returns (glyph, shadow) layers, both full-size RGBA, glyph=white."""
    glyph = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    gdraw = ImageDraw.Draw(glyph)

    cx, cy = size / 2, size * 0.50
    fin_count = 5
    glyph_w = size * 0.56
    glyph_h = size * 0.34
    gap = glyph_w * 0.085 / (fin_count - 1) * (fin_count - 1)  # placeholder, recomputed below

    left = cx - glyph_w / 2
    top = cy - glyph_h / 2
    bottom = cy + glyph_h / 2

    gap_w = glyph_w * 0.045
    fin_w = (glyph_w - gap_w * (fin_count - 1)) / fin_count

    for i in range(fin_count):
        x0 = left + i * (fin_w + gap_w)
        x1 = x0 + fin_w
        rounded_bar(gdraw, [x0, top, x1, bottom], WHITE)

    # Header manifold pipe beneath the fins, unifying the shape.
    manifold_h = size * 0.05
    manifold_top = bottom - size * 0.012
    rounded_bar(gdraw, [left - fin_w * 0.35, manifold_top, left + glyph_w + fin_w * 0.35, manifold_top + manifold_h], WHITE)

    # TRV valve head: body + small angled dial knob, sitting at the
    # bottom-left where a real thermostatic valve mounts on the pipe.
    valve_cx = left + fin_w * 0.15
    valve_cy = manifold_top + manifold_h + size * 0.075
    valve_r = size * 0.085
    gdraw.ellipse(
        [valve_cx - valve_r, valve_cy - valve_r, valve_cx + valve_r, valve_cy + valve_r],
        fill=WHITE,
    )
    # short connecting neck from manifold to valve body
    neck_w = size * 0.05
    gdraw.rectangle(
        [valve_cx - neck_w / 2, manifold_top + manifold_h - size * 0.01, valve_cx + neck_w / 2, valve_cy],
        fill=WHITE,
    )
    # dial notches on the valve head (three short ticks) for texture
    tick_r_outer = valve_r * 0.72
    tick_r_inner = valve_r * 0.42
    import math

    for ang_deg in (-55, 0, 55):
        ang = math.radians(ang_deg - 90)
        x0 = valve_cx + tick_r_inner * math.cos(ang)
        y0 = valve_cy + tick_r_inner * math.sin(ang)
        x1 = valve_cx + tick_r_outer * math.cos(ang)
        y1 = valve_cy + tick_r_outer * math.sin(ang)
        gdraw.line([x0, y0, x1, y1], fill=(255, 87, 51, 255), width=max(2, int(size * 0.012)))

    # Shadow: blurred, offset copy of the same alpha silhouette.
    shadow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    silhouette = glyph.split()[3]
    shadow.putalpha(silhouette)
    shadow_rgb = Image.new("RGBA", (size, size), SHADOW)
    shadow = Image.composite(shadow_rgb, Image.new("RGBA", (size, size), (0, 0, 0, 0)), silhouette)
    offset = int(size * 0.018)
    shifted = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    shifted.paste(shadow, (offset, offset), shadow)
    shifted = shifted.filter(ImageFilter.GaussianBlur(size * 0.02))

    return glyph, shifted


def make_master() -> Image.Image:
    size = MASTER
    radius = size * 0.225

    mask = squircle_mask(size, radius)
    badge = gradient_layer(size, mask)
    sheen = glass_highlight(size, mask)
    badge = Image.alpha_composite(badge, sheen)

    # Subtle inner rim for definition.
    rim = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ImageDraw.Draw(rim).rounded_rectangle(
        [size * 0.004, size * 0.004, size - size * 0.004, size - size * 0.004],
        radius=radius,
        outline=(255, 255, 255, 60),
        width=max(2, int(size * 0.004)),
    )
    badge = Image.alpha_composite(badge, rim)

    glyph, shadow = radiator_glyph(size)
    badge = Image.alpha_composite(badge, shadow)
    badge = Image.alpha_composite(badge, glyph)

    return badge


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    master = make_master()

    master.resize((512, 512), Image.LANCZOS).save(OUT_DIR / "icon@2x.png")
    master.resize((256, 256), Image.LANCZOS).save(OUT_DIR / "icon.png")
    master.resize((512, 512), Image.LANCZOS).save(OUT_DIR / "logo@2x.png")
    master.resize((256, 256), Image.LANCZOS).save(OUT_DIR / "logo.png")

    preview_dir = Path(__file__).resolve().parent
    master.resize((512, 512), Image.LANCZOS).save(preview_dir / "icon_preview.png")

    print("Written:", sorted(p.name for p in OUT_DIR.glob("*.png")))


if __name__ == "__main__":
    main()
