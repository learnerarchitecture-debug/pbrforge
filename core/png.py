"""Minimal PNG reader/writer, standard library only.

Why hand-roll this instead of using Pillow: the installable single-file build has
to work on a machine that has never run pip. Pillow is great and `doctor` will
report it as available, but the *portable* path cannot depend on it. Eighty lines
of zlib and struct buys a dependency-free image path, which is the whole point of
the portable build.

Scope, deliberately narrow:
  - 8-bit and 16-bit, greyscale / RGB / RGBA, non-interlaced
  - read: any PNG matching the above
  - write: our own files, deterministic bytes for a given input

NOT supported, on purpose: palette, interlaced (Adam7), 1/2/4-bit, 16-bit with a
tRNS chunk, APNG. If a user drops such a file, `read()` raises a clear error
naming the unsupported feature rather than producing plausible-looking garbage.
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass
from pathlib import Path

PNG_SIG = b"\x89PNG\r\n\x1a\n"

_COLOR_TYPES = {
    0: ("grey", 1),
    2: ("rgb", 3),
    4: ("grey+alpha", 2),
    6: ("rgba", 4),
}


class PngError(ValueError):
    """Raised with a human-readable reason. Never a bare struct.error."""


@dataclass
class Image:
    """Row-major pixel data. `data` is bytes, channels per pixel in `channels`.

    Kept deliberately dumb: this is the interchange format between stages, not a
    processing type. Stages convert to numpy when numpy is available.
    """

    width: int
    height: int
    channels: int
    bit_depth: int
    data: bytes

    @property
    def is_16bit(self) -> bool:
        return self.bit_depth == 16

    @property
    def row_bytes(self) -> int:
        return self.width * self.channels * (2 if self.is_16bit else 1)

    def sample(self, x: int, y: int) -> tuple[int, ...]:
        """One pixel as 0..65535 integers, regardless of bit depth."""
        x %= self.width          # wrap: makes tiling correctness trivially testable
        y %= self.height
        step = self.row_bytes
        off = y * step + x * self.channels * (2 if self.is_16bit else 1)
        if self.is_16bit:
            return tuple(struct.unpack_from(">H", self.data, off + 2 * c)[0]
                         for c in range(self.channels))
        return tuple(self.data[off + c] for c in range(self.channels))

    def mean_channel(self, channel: int = 0) -> float:
        """Mean of one channel in 0..1. Used for the albedo-plausibility QC check."""
        scale = 65535.0 if self.is_16bit else 255.0
        total = 0
        for y in range(self.height):
            for x in range(self.width):
                total += self.sample(x, y)[channel]
        return total / (self.width * self.height * scale)


def _dechunk(blob: bytes) -> tuple[list[tuple[bytes, bytes]], bytes]:
    """Split a PNG into (type, data) chunks and concatenated IDAT payload."""
    if not blob.startswith(PNG_SIG):
        raise PngError("not a PNG: the 8-byte signature is missing or wrong")
    pos = len(PNG_SIG)
    chunks: list[tuple[bytes, bytes]] = []
    idat = bytearray()
    while pos + 8 <= len(blob):
        (length,) = struct.unpack_from(">I", blob, pos)
        ctype = blob[pos + 4:pos + 8]
        start = pos + 8
        end = start + length
        if end + 4 > len(blob):
            raise PngError(f"truncated PNG: chunk {ctype!r} claims {length} bytes "
                           f"but only {len(blob) - start} remain")
        payload = blob[start:end]
        crc_stored = struct.unpack_from(">I", blob, end)[0]
        crc_actual = zlib.crc32(ctype + payload) & 0xFFFFFFFF
        if crc_stored != crc_actual:
            raise PngError(f"PNG chunk {ctype.decode('ascii', 'replace')!r} failed its CRC check "
                           f"(stored {crc_stored:08x}, computed {crc_actual:08x})")
        chunks.append((ctype, payload))
        if ctype == b"IDAT":
            idat += payload
        if ctype == b"IEND":
            break
        pos = end + 4
    return chunks, bytes(idat)


def _unfilter(raw: bytes, height: int, row_bytes: int, bpp: int) -> bytearray:
    """Undo PNG per-row filtering. `bpp` is bytes per pixel, not bits."""
    out = bytearray(height * row_bytes)
    prev = bytearray(row_bytes)
    pos = 0
    for y in range(height):
        if pos >= len(raw):
            raise PngError(f"truncated image data: ran out at row {y} of {height}")
        ftype = raw[pos]
        pos += 1
        line = bytearray(raw[pos:pos + row_bytes])
        if len(line) != row_bytes:
            raise PngError(f"truncated image data on row {y}")
        pos += row_bytes
        if ftype == 0:
            pass
        elif ftype == 1:
            for i in range(bpp, row_bytes):
                line[i] = (line[i] + line[i - bpp]) & 0xFF
        elif ftype == 2:
            for i in range(row_bytes):
                line[i] = (line[i] + prev[i]) & 0xFF
        elif ftype == 3:
            for i in range(row_bytes):
                left = line[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + ((left + prev[i]) >> 1)) & 0xFF
        elif ftype == 4:
            for i in range(row_bytes):
                a = line[i - bpp] if i >= bpp else 0
                b = prev[i]
                c = prev[i - bpp] if i >= bpp else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[i] = (line[i] + pr) & 0xFF
        else:
            raise PngError(f"unknown row filter type {ftype} on row {y}")
        out[y * row_bytes:(y + 1) * row_bytes] = line
        prev = line
    return out


def read(path: str | Path) -> Image:
    p = Path(path)
    try:
        blob = p.read_bytes()
    except OSError as exc:
        raise PngError(f"cannot read {p}: {exc.strerror or exc}") from exc
    if not blob:
        raise PngError(f"{p.name} is empty (0 bytes)")

    chunks, idat = _dechunk(blob)
    ihdr = next((d for t, d in chunks if t == b"IHDR"), None)
    if ihdr is None or len(ihdr) != 13:
        raise PngError("PNG has no valid IHDR chunk")

    width, height, bit_depth, color_type, compression, filt, interlace = struct.unpack(">IIBBBBB", ihdr)
    if width == 0 or height == 0:
        raise PngError(f"PNG declares a zero dimension ({width}x{height})")
    if compression != 0:
        raise PngError(f"unsupported PNG compression method {compression}")
    if filt != 0:
        raise PngError(f"unsupported PNG filter method {filt}")
    if interlace != 0:
        raise PngError("interlaced (Adam7) PNGs are not supported by the portable reader — "
                       "re-save without interlacing, or install Pillow")
    if color_type not in _COLOR_TYPES:
        raise PngError(f"unsupported PNG colour type {color_type} "
                       f"(palette images are not supported — convert to RGB/RGBA first)")
    if bit_depth not in (8, 16):
        raise PngError(f"unsupported PNG bit depth {bit_depth} (only 8 and 16 are supported)")

    _, channels = _COLOR_TYPES[color_type]
    row_bytes = width * channels * (bit_depth // 8)
    try:
        raw = zlib.decompress(bytes(idat))
    except zlib.error as exc:
        raise PngError(f"PNG image data is corrupt: {exc}") from exc

    pixels = _unfilter(raw, height, row_bytes, channels * (bit_depth // 8))

    # A 16-bit PNG may carry a palette-free tRNS chunk; refuse rather than guess.
    if any(t == b"tRNS" for t, _ in chunks) and bit_depth == 16:
        raise PngError("16-bit PNG with a tRNS chunk is not supported")

    return Image(width, height, channels, bit_depth, bytes(pixels))


def write(path: str | Path, image: Image, *, compress: int = 6) -> Path:
    """Write an 8/16-bit PNG with deterministic bytes.

    Determinism matters: the pipeline cache is content-addressed, so writing the
    same image twice must produce the same bytes or every run misses the cache.
    That means a fixed filter type and a fixed zlib level -- no heuristics.
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)

    if image.channels not in (1, 2, 3, 4):
        raise PngError(f"cannot write {image.channels} channels")
    if image.bit_depth not in (8, 16):
        raise PngError(f"cannot write {image.bit_depth}-bit")
    expected = image.height * image.row_bytes
    if len(image.data) != expected:
        raise PngError(f"pixel buffer is {len(image.data)} bytes, expected {expected} "
                       f"for {image.width}x{image.height}x{image.channels} @ {image.bit_depth}-bit")

    color_type = {1: 0, 2: 4, 3: 2, 4: 6}[image.channels]

    # Filter type 0 (None) on every row: larger files, but deterministic and fast
    # in pure Python. Filtering in Python would cost more than the compression saves.
    stride = image.row_bytes
    rows = bytearray()
    for y in range(image.height):
        rows.append(0)
        rows += image.data[y * stride:(y + 1) * stride]

    def chunk(ctype: bytes, payload: bytes) -> bytes:
        return (struct.pack(">I", len(payload)) + ctype + payload
                + struct.pack(">I", zlib.crc32(ctype + payload) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", image.width, image.height, image.bit_depth,
                       color_type, 0, 0, 0)
    blob = (PNG_SIG
            + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(bytes(rows), compress))
            + chunk(b"IEND", b""))
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_bytes(blob)
    tmp.replace(p)          # atomic on the same volume: no half-written PNGs in the cache
    return p


def from_grey(values: list[int], width: int, height: int, bit_depth: int = 8) -> Image:
    if bit_depth == 16:
        data = b"".join(struct.pack(">H", max(0, min(65535, v))) for v in values)
    else:
        data = bytes(max(0, min(255, v)) for v in values)
    return Image(width, height, 1, bit_depth, data)
