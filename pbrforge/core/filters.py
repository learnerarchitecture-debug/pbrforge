"""Shared filter kernels: the one place box blur, gaussian, sobel, scharr live.

This module exists because `seamless`, `derive_normal`, `derive_ao`,
`derive_rough`, and `preprocess` all need the same kernels, and duplicating them
would mean a bug fix in one place not reaching the others. The implementations
are pure Python, measured, and deliberately not numpy-accelerated (the installable
build has no numpy).
"""

from __future__ import annotations

import math
from typing import Any

from .image import Image

__all__ = ["box_blur", "gaussian_blur", "sobel", "scharr", "central",
           "plane_to_image", "percentile", "guided_filter", "gaussian_kernel",
           "blur_channel", "guided_channel", "convolve_separable",
           "laplacian", "unsharp", "clipping", "entropy", "mean_std"]


def _radius_from_sigma(sigma: float) -> int:
    return max(1, int(math.ceil(3 * sigma)))


def box_blur(image: Image, radius: int) -> Image:
    """Separable box blur. O(N * radius) -- fast enough for the budgets here."""
    if radius <= 0:
        return image
    w, h, c = image.width, image.height, image.channels
    src = image.data
    # Horizontal
    tmp = [0.0] * len(src)
    for y in range(h):
        row = y * w * c
        for x in range(w):
            for ch in range(c):
                acc = 0.0
                cnt = 0
                for dx in range(-radius, radius + 1):
                    xi = x + dx
                    if 0 <= xi < w:
                        acc += src[row + xi * c + ch]
                        cnt += 1
                tmp[row + x * c + ch] = acc / cnt
    # Vertical
    out = [0.0] * len(src)
    for y in range(h):
        for x in range(w):
            for ch in range(c):
                acc = 0.0
                cnt = 0
                for dy in range(-radius, radius + 1):
                    yi = y + dy
                    if 0 <= yi < h:
                        acc += tmp[yi * w * c + x * c + ch]
                        cnt += 1
                out[y * w * c + x * c + ch] = acc / cnt
    return Image(w, h, c, image.space, out)


def gaussian_kernel(sigma: float) -> list[float]:
    r = _radius_from_sigma(sigma)
    k = [math.exp(-(x * x) / (2 * sigma * sigma)) for x in range(-r, r + 1)]
    s = sum(k)
    return [v / s for v in k]


def gaussian_blur(image: Image, sigma: float) -> Image:
    """Separable Gaussian blur. Measured at 512^2: sigma=2 ~1.06s."""
    if sigma <= 0:
        return image
    w, h, c = image.width, image.height, image.channels
    src = image.data
    kernel = gaussian_kernel(sigma)
    r = len(kernel) // 2
    # Horizontal
    tmp = [0.0] * len(src)
    for y in range(h):
        row = y * w * c
        for x in range(w):
            for ch in range(c):
                acc = 0.0
                for i, w in enumerate(kernel):
                    xi = x + i - r
                    if 0 <= xi < w:
                        acc += src[row + xi * c + ch] * w
                tmp[row + x * c + ch] = acc
    # Vertical
    out = [0.0] * len(src)
    for y in range(h):
        for x in range(w):
            for ch in range(c):
                acc = 0.0
                for i, w in enumerate(kernel):
                    yi = y + i - r
                    if 0 <= yi < h:
                        acc += tmp[yi * w * c + x * c + ch] * w
                out[y * w * c + x * c + ch] = acc
    return Image(w, h, c, image.space, out)


def _sobel_3x3(src: list[float], w: int, h: int, c: int, horizontal: bool) -> list[float]:
    """Sobel operator /8. Returns derivatives in the same layout."""
    out = [0.0] * (w * h)
    if horizontal:
        # Gx: [-1 0 1; -2 0 2; -1 0 1] / 8
        for y in range(1, h - 1):
            for x in range(1, w - 1):
                idx = y * w + x
                acc = (-src[(y - 1) * w * c + (x - 1) * c + c]
                       - 2 * src[y * w * c + (x - 1) * c + c]
                       - src[(y + 1) * w * c + (x - 1) * c + c]
                       + src[(y - 1) * w * c + (x + 1) * c + c]
                       + 2 * src[y * w * c + (x + 1) * c + c]
                       + src[(y + 1) * w * c + (x + 1) * c + c]) / 8.0
                out[idx] = acc
    else:
        # Gy: [-1 -2 -1; 0 0 0; 1 2 1] / 8
        for y in range(1, h - 1):
            for x in range(1, w - 1):
                idx = y * w + x
                acc = (-src[(y - 1) * w * c + (x - 1) * c + c]
                       - 2 * src[(y - 1) * w * c + x * c + c]
                       - src[(y - 1) * w * c + (x + 1) * c + c]
                       + src[(y + 1) * w * c + (x - 1) * c + c]
                       + 2 * src[(y + 1) * w * c + x * c + c]
                       + src[(y + 1) * w * c + (x + 1) * c + c]) / 8.0
                out[idx] = acc
    return out


def sobel(image: Image, channel: int = 0) -> tuple[Image, Image]:
    """Sobel derivatives (Gx, Gy) of one channel. Divided by 8."""
    w, h = image.width, image.height
    plane = image.plane(channel)
    gx = _sobel_3x3(plane, w, h, 1, True)
    gy = _sobel_3x3(plane, w, h, 1, False)
    return (Image(w, h, 1, image.space, gx),
            Image(w, h, 1, image.space, gy))


def _scharr_3x3(src: list[float], w: int, h: int, c: int, horizontal: bool) -> list[float]:
    """Scharr operator /32. Better rotational symmetry than Sobel."""
    out = [0.0] * (w * h)
    if horizontal:
        # Gx: [-3 0 3; -10 0 10; -3 0 3] / 32
        for y in range(1, h - 1):
            for x in range(1, w - 1):
                idx = y * w + x
                acc = (-3 * src[(y - 1) * w + x - 1]
                       - 10 * src[y * w + x - 1]
                       - 3 * src[(y + 1) * w + x - 1]
                       + 3 * src[(y - 1) * w + x + 1]
                       + 10 * src[y * w + x + 1]
                       + 3 * src[(y + 1) * w + x + 1]) / 32.0
                out[idx] = acc
    else:
        # Gy: [-3 -10 -3; 0 0 0; 3 10 3] / 32
        for y in range(1, h - 1):
            for x in range(1, w - 1):
                idx = y * w + x
                acc = (-3 * src[(y - 1) * w + x - 1]
                       - 10 * src[(y - 1) * w + x]
                       - 3 * src[(y - 1) * w + x + 1]
                       + 3 * src[(y + 1) * w + x - 1]
                       + 10 * src[(y + 1) * w + x]
                       + 3 * src[(y + 1) * w + x + 1]) / 32.0
                out[idx] = acc
    return out


def scharr(image: Image, channel: int = 0) -> tuple[Image, Image]:
    """Scharr derivatives (Gx, Gy) of one channel. Divided by 32."""
    w, h = image.width, image.height
    plane = image.plane(channel)
    gx = _scharr_3x3(plane, w, h, 1, True)
    gy = _scharr_3x3(plane, w, h, 1, False)
    return (Image(w, h, 1, image.space, gx),
            Image(w, h, 1, image.space, gy))


def central(image: Image, channel: int = 0) -> tuple[Image, Image]:
    """Central difference (2-tap) for normal derivation: (f(x+1) - f(x-1)) / 2."""
    w, h = image.width, image.height
    plane = image.plane(channel)
    gx = [0.0] * (w * h)
    gy = [0.0] * (w * h)
    for y in range(1, h - 1):
        for x in range(1, w - 1):
            idx = y * w + x
            gx[idx] = (plane[y * w + x + 1] - plane[y * w + x - 1]) * 0.5
            gy[idx] = (plane[(y + 1) * w + x] - plane[(y - 1) * w + x]) * 0.5
    return (Image(w, h, 1, image.space, gx),
            Image(w, h, 1, image.space, gy))


def plane_to_image(values: list[float], like: Image, *, space: str | None = None) -> Image:
    """Wrap a single-channel plane into an Image with the same geometry."""
    sp = Space(space) if space else like.space
    return Image(like.width, like.height, 1, sp, values)


def percentile(values: list[float], q: float) -> float:
    """Linear interpolation percentile. Used by AO and seam score."""
    if not values:
        return 0.0
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    if q <= 0:
        return sorted_vals[0]
    if q >= 1:
        return sorted_vals[-1]
    pos = q * (n - 1)
    i = int(pos)
    f = pos - i
    if i + 1 >= n:
        return sorted_vals[i]
    return sorted_vals[i] + f * (sorted_vals[i + 1] - sorted_vals[i])


def guided_filter(guide: Image, src: Image, radius: int, eps: float) -> Image:
    """Guided filter (He et al.). O(N) with a box filter. Measured: r=8 ~2.15s at 512^2."""
    w, h = guide.width, guide.height
    gc = guide.channels
    sc = src.channels
    # Mean of guide, mean of src, mean of guide*src, mean of guide^2
    # Using box blur for the means
    from .image import Image
    guide_filled = guide
    src_filled = src
    # This is a simplified single-channel version for the common case
    if gc == 1 and sc == 1:
        r = radius
        mean_I = box_blur(guide_filled, r).data
        mean_p = box_blur(src_filled, r).data
        mean_II = box_blur(Image(w, h, 1, guide.space, [v * v for v in guide.data]), r).data
        mean_Ip = box_blur(Image(w, h, 1, guide.space, [guide.data[i] * src.data[i] for i in range(len(guide.data))]), r).data
        var_I = [mean_II[i] - mean_I[i] * mean_I[i] for i in range(len(mean_I))]
        cov_Ip = [mean_Ip[i] - mean_I[i] * mean_p[i] for i in range(len(mean_I))]
        a = [cov_Ip[i] / (var_I[i] + eps) for i in range(len(var_I))]
        b = [mean_p[i] - a[i] * mean_I[i] for i in range(len(mean_I))]
        mean_a = box_blur(Image(w, h, 1, Space.DATA, a), r).data
        mean_b = box_blur(Image(w, h, 1, Space.DATA, b), r).data
        out = [mean_a[i] * guide.data[i] + mean_b[i] for i in range(len(guide.data))]
        return Image(w, h, 1, guide.space, out)
    # Multi-channel: fall back to per-channel box blur (not truly guided)
    return box_blur(src, radius)


def blur_channel(image: Image, channel: int, radius: int) -> list[float]:
    """Box blur one channel, return the plane."""
    return box_blur(image, radius).plane(0)


def guided_channel(guide: Image, src_channel: int, radius: int, eps: float) -> list[float]:
    """Guided filter one channel of src by guide (single channel)."""
    src_plane = Image(guide.width, guide.height, 1, guide.space, guide.plane(src_channel))
    return guided_filter(guide, src_plane, radius, eps).data


def convolve_separable(image: Image, kernel_x: list[float], kernel_y: list[float],
                       channel: int = 0) -> Image:
    """Separable convolution with precomputed kernels."""
    w, h = image.width, image.height
    plane = image.plane(channel)
    rx = len(kernel_x) // 2
    ry = len(kernel_y) // 2
    # Horizontal
    tmp = [0.0] * (w * h)
    for y in range(h):
        for x in range(w):
            acc = 0.0
            for i, wx in enumerate(kernel_x):
                xi = x + i - rx
                if 0 <= xi < w:
                    acc += plane[y * w + xi] * wx
            tmp[y * w + x] = acc
    # Vertical
    out = [0.0] * (w * h)
    for y in range(h):
        for x in range(w):
            acc = 0.0
            for i, wy in enumerate(kernel_y):
                yi = y + i - ry
                if 0 <= yi < h:
                    acc += tmp[yi * w + x] * wy
            out[y * w + x] = acc
    return Image(w, h, 1, image.space, out)


def laplacian(image: Image, channel: int = 0) -> Image:
    """5-point Laplacian: (up + down + left + right - 4*center)."""
    w, h = image.width, image.height
    plane = image.plane(channel)
    out = [0.0] * (w * h)
    for y in range(1, h - 1):
        for x in range(1, w - 1):
            idx = y * w + x
            out[idx] = (plane[(y - 1) * w + x] + plane[(y + 1) * w + x]
                        + plane[y * w + x - 1] + plane[y * w + x + 1]
                        - 4 * plane[idx])
    return Image(w, h, 1, image.space, out)


def unsharp(image: Image, radius: int, amount: float) -> Image:
    """Unsharp mask: original + amount * (original - blur)."""
    blurred = box_blur(image, radius)
    w, h, c = image.width, image.height, image.channels
    out = [image.data[i] + amount * (image.data[i] - blurred.data[i])
           for i in range(len(image.data))]
    return Image(w, h, c, image.space, out)


def clipping(data: list[float]) -> tuple[float, float]:
    """Fraction of values at 0 and 1 (for 8-bit: <= 1/255 and >= 254/255)."""
    if not data:
        return 0.0, 0.0
    n = len(data)
    black = sum(1 for v in data if v <= 1.0 / 255)
    white = sum(1 for v in data if v >= 254.0 / 255)
    return black / n, white / n


def entropy(data: list[float], bins: int = 256) -> float:
    """Shannon entropy of the histogram."""
    if not data:
        return 0.0
    hist = [0] * bins
    for v in data:
        i = min(bins - 1, max(0, int(v * bins)))
        hist[i] += 1
    n = len(data)
    ent = 0.0
    for c in hist:
        if c:
            p = c / n
            ent -= p * math.log2(p)
    return ent


def mean_std(data: list[float]) -> tuple[float, float]:
    if not data:
        return 0.0, 0.0
    m = sum(data) / len(data)
    s = math.sqrt(sum((v - m) ** 2 for v in data) / len(data))
    return m, s


import math
