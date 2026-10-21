"""Colours: DICOM CIELab values to RGB, and a palette for objects that carry none."""

from __future__ import annotations

from collections.abc import Sequence

from dicom_viewer.annotations.model import RGB

# Twelve distinguishable colours for items without one of their own (SR, GSPS).
PALETTE: tuple[RGB, ...] = (
    (230, 25, 75),
    (60, 180, 75),
    (255, 225, 25),
    (0, 130, 200),
    (245, 130, 48),
    (145, 30, 180),
    (70, 240, 240),
    (240, 50, 230),
    (210, 245, 60),
    (250, 190, 212),
    (0, 128, 128),
    (255, 215, 180),
)


def default_color(index: int) -> RGB:
    """The palette colour for the item at `index` in its layer; wraps around."""
    return PALETTE[index % len(PALETTE)]


def spread(layers: Sequence) -> None:
    """Palette colours for the items that brought none, counted across `layers`.

    Each adapter numbers its own items, so several objects without colours (one
    segmentation per reader, say) would all come out in the palette's first
    colour. Colours some item brought itself are left out while others remain.
    """
    taken = {tuple(item.color) for layer in layers for item in layer.items if item.own_color}
    free = [color for color in PALETTE if color not in taken] or list(PALETTE)
    count = 0
    for layer in layers:
        colors = {}
        for item in layer.items:
            if not item.own_color:
                colors[item.id] = free[count % len(free)]
                count += 1
        recolor = getattr(layer, "recolor", None)
        if colors and recolor is not None:
            recolor(colors)


def cielab_to_rgb(lab: Sequence[float]) -> RGB:
    """CIELab (L* 0-100, a* and b* about -128..127) to sRGB 0-255, D65 white, clipped."""
    lightness, a, b = (float(x) for x in lab)
    fy = (lightness + 16.0) / 116.0
    fx = fy + a / 500.0
    fz = fy - b / 200.0

    def inverse(t: float) -> float:
        return t**3 if t > 6.0 / 29.0 else 3.0 * (6.0 / 29.0) ** 2 * (t - 4.0 / 29.0)

    x, y, z = 0.95047 * inverse(fx), 1.0 * inverse(fy), 1.08883 * inverse(fz)
    linear = (
        3.2406 * x - 1.5372 * y - 0.4986 * z,
        -0.9689 * x + 1.8758 * y + 0.0415 * z,
        0.0557 * x - 0.2040 * y + 1.0570 * z,
    )

    def gamma(c: float) -> float:
        c = min(1.0, max(0.0, c))
        return 1.055 * c ** (1.0 / 2.4) - 0.055 if c > 0.0031308 else 12.92 * c

    r, g, b = (int(round(255.0 * gamma(c))) for c in linear)
    return (r, g, b)


def dicom_cielab_to_rgb(values: Sequence[int]) -> RGB:
    """RecommendedDisplayCIELabValue (three uint16, PS3.3 C.10.7.1.1) to sRGB 0-255."""
    lightness, a, b = (int(v) for v in values)
    return cielab_to_rgb(
        (lightness / 65535.0 * 100.0, a / 65535.0 * 255.0 - 128.0, b / 65535.0 * 255.0 - 128.0)
    )
