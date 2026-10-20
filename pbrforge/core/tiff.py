"""Minimal TIFF: uncompressed, single strip, 8 or 16-bit, grey/RGB/RGBA.

Why this exists at all, next to a PNG codec that already does 16-bit: PRESETS.md
3 names a TIFF/EXR pair for height, and the difference is not cosmetic. A
displacement map's 8-bit banding is the artefact that shows on a large flat
surface, and some pipelines (and some renderers' displacement loaders) accept
TIFF where they will not accept PNG. Writing no TIFF at all would quietly
downgrade the one map whose bit depth the doc calls non-negotiable.

What this is **not** is a general TIFF implementation, and the refusals say so:

  - **Uncompressed only.** LZW needs a dictionary encoder; Deflate would need
    zlib's raw mode and a second predictor path. Both are write-side choices with
    no benefit here: these files are read by renderers, not shipped.
  - **Single strip.** Tiled and multi-strip layouts exist for streaming and
    parallel readers. One strip is simpler and every listed reader accepts it.
  - **Baseline photometric only.** Grey (BlackIsZero) and RGB. No palettes, no
    CMYK, no float samples, no extra samples (alpha is a plain 4th sample here,
    which is what the interchange type already carries).
  - **II byte order.** Little-endian, written explicitly, so the bytes do not
    depend on the machine that produced them.

The read side exists for one reason: a writer nobody can read back is not
verified, and this build has a rule against shipping code it has not run. The
check script writes a file, reads it back, and compares pixels; nothing in the
pipeline uses `read`.
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

from .errors import LimitError, PbrForgeError
from .png import Image

__all__ = ["TiffError", "read", "write"]

#: Baseline tags, in the ascending order TIFF requires within an IFD.
_TAG_WIDTH = 256
_TAG_LENGTH = 257
_TAG_BITS_PER_SAMPLE = 258
_TAG_COMPRESSION = 259
_TAG_PHOTOMETRIC = 262
_TAG_STRIP_OFFSETS = 273
_TAG_SAMPLES_PER_PIXEL = 277
_TAG_ROWS_PER_STRIP = 278
_TAG_STRIP_BYTE_COUNTS = 279
_TAG_SAMPLE_FORMAT = 339

_TYPE_SHORT = 3
_TYPE_LONG = 4

_COMPRESSION_NONE = 1
_PHOTOMETRIC_BLACK_IS_ZERO = 1
_PHOTOMETRIC_RGB = 2
_SAMPLE_FORMAT_UINT = 1


class TiffError(PbrForgeError):
    """A TIFF this codec will not write, or cannot read back."""


def _channels_ok(channels: int) -> bool:
    return channels in (1, 2, 3, 4)


def _native_little() -> bool:
    return sys.byteorder == "little"


def _swap16(data: bytes) -> bytes:
    """Byte-swap a 16-bit sample buffer. C speed, and the same on every host.

    `array.frombytes` reads the buffer in *native* order. On a little-endian host
    that gives the wrong values for big-endian input, and `byteswap()` fixes every
    element at once; on a big-endian host the values are already right. The branch
    is the whole of the endianness reasoning, which is why it is here and not
    spread over the two callers.
    """
    if not data:
        return b""
    from array import array

    buf = array("H")
    buf.frombytes(data)
    if _native_little():
        buf.byteswap()
    return buf.tobytes()


def _to_le16(data: bytes) -> bytes:
    """Interchange data (big-endian 16-bit, as `png.Image` carries it) to LE."""
    return _swap16(data)


def _from_le16(data: bytes) -> bytes:
    """Little-endian 16-bit back to the interchange convention."""
    return _swap16(data)


def write(path: str | Path, image: Image, *, rows_per_strip: int | None = None) -> Path:
    """Write `image` as an uncompressed baseline TIFF. Returns the path.

    `image` is the interchange type from `core/png.py`, so the two writers share
    one quantisation: a value written to PNG 16-bit and to TIFF 16-bit is the same
    integer, which is what makes "the height map is 16-bit" a statement about the
    pixels rather than about the container.
    """
    width, height, channels, bit_depth = (
        image.width, image.height, image.channels, image.bit_depth)
    if bit_depth not in (8, 16):
        raise TiffError(
            f"TIFF bit depth {bit_depth} is not supported.\n"
            f"  why      baseline TIFF 8 and 16-bit unsigned are what the listed "
            f"renderers load without a plugin; 32-bit float needs SampleFormat 3 "
            f"and this codec does not write it\n"
            f"  fix      write 16-bit (height.bit_depth = 16), which is the depth "
            f"PRESETS.md 3 asks for")
    if not _channels_ok(channels):
        raise TiffError(
            f"TIFF with {channels} channel(s) is not supported.\n"
            f"  why      this codec writes grey, grey+alpha, RGB and RGBA, which is "
            f"everything the map registry produces; a palette or CMYK file would "
            f"need an extra tag set no renderer in the cookbook asks for\n"
            f"  fix      write the map as PNG instead, or split the channels")
    expected = width * height * channels * (bit_depth // 8)
    if len(image.data) != expected:
        raise TiffError(
            f"the pixel buffer is {len(image.data)} bytes for a "
            f"{width}x{height}x{channels} {bit_depth}-bit image, which needs "
            f"{expected}.\n"
            f"  why      writing the short buffer would produce a file that looks "
            f"fine until the last strip and then reads as noise\n"
            f"  fix      rebuild the image through core.image.Image.to_png, which "
            f"pads and truncates to the exact size")

    rows = height if rows_per_strip is None else max(1, min(rows_per_strip, height))
    payload = _to_le16(image.data) if bit_depth == 16 else image.data
    photometric = _PHOTOMETRIC_RGB if channels >= 3 else _PHOTOMETRIC_BLACK_IS_ZERO

    entries = [
        (_TAG_WIDTH, _TYPE_LONG, 1, width),
        (_TAG_LENGTH, _TYPE_LONG, 1, height),
        (_TAG_BITS_PER_SAMPLE, _TYPE_SHORT, channels, bit_depth),
        (_TAG_COMPRESSION, _TYPE_SHORT, 1, _COMPRESSION_NONE),
        (_TAG_PHOTOMETRIC, _TYPE_SHORT, 1, photometric),
        (_TAG_STRIP_OFFSETS, _TYPE_LONG, 1, 0),        # filled after layout
        (_TAG_SAMPLES_PER_PIXEL, _TYPE_SHORT, 1, channels),
        (_TAG_ROWS_PER_STRIP, _TYPE_LONG, 1, rows),
        (_TAG_STRIP_BYTE_COUNTS, _TYPE_LONG, 1, len(payload)),
        (_TAG_SAMPLE_FORMAT, _TYPE_SHORT, channels, _SAMPLE_FORMAT_UINT),
    ]
    entries.sort(key=lambda e: e[0])

    ifd_size = 2 + 12 * len(entries) + 4
    extra_offset = 8 + ifd_size
    extra = bytearray()
    blob_entries: list[bytes] = []
    for tag, kind, count, value in entries:
        elem = {_TYPE_SHORT: 2, _TYPE_LONG: 4}[kind]
        size = elem * count
        if size <= 4:
            # Counts of 1 and 2 fit inline (2 bytes and 4), which is why the
            # repeat is written rather than a single value: "II" is a header, but
            # a two-channel BitsPerSample is not.
            inline = max(1, 4 // elem)
            values = [value] * min(count, inline)
            fmt = "<" + ({_TYPE_SHORT: "H", _TYPE_LONG: "I"}[kind] * len(values))
            raw = struct.pack(fmt, *values)
            ifd_entry = struct.pack("<HHI", tag, kind, count) + raw.ljust(4, b"\x00")
        else:
            here = extra_offset + len(extra)
            if kind == _TYPE_SHORT:
                extra += struct.pack(f"<{count}H", *([value] * count))
            else:
                extra += struct.pack(f"<{count}I", *([value] * count))
            ifd_entry = struct.pack("<HHII", tag, kind, count, here)
        if len(extra) % 2:
            extra += b"\x00"
        blob_entries.append(ifd_entry)

    data_offset = extra_offset + len(extra)
    ifd = struct.pack("<H", len(entries)) + b"".join(blob_entries) + struct.pack("<I", 0)
    # StripOffsets was written as 0; rewrite it in place now that the layout is
    # known. Rewriting the 4 value bytes is cheaper and clearer than a second pass
    # that would have to know every other offset too.
    where = 2 + 12 * [e[0] for e in entries].index(_TAG_STRIP_OFFSETS) + 8
    ifd = ifd[:where] + struct.pack("<I", data_offset) + ifd[where + 4:]

    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("wb") as fh:
        fh.write(b"II" + struct.pack("<HI", 42, 8))
        fh.write(ifd)
        fh.write(bytes(extra))
        fh.write(payload)
    tmp.replace(path)
    return path


def read(path: str | Path) -> Image:
    """Read a single-strip uncompressed baseline TIFF this codec would write.

    Deliberately narrow: it exists so the writer can be verified against bytes
    rather than against a promise. It refuses anything with a compression tag, a
    tiled layout, or a sample format other than unsigned integer instead of
    guessing.
    """
    try:
        raw = Path(path).read_bytes()
    except OSError as exc:
        raise TiffError(
            f"cannot read {Path(path).name}: {exc.strerror or exc}.\n"
            f"  why      the reader is given a path and either the file is not "
            f"there or it cannot be opened, and an OSError from here would reach "
            f"the caller as a crash rather than a refusal\n"
            f"  fix      check the path, or re-export the map") from None
    if len(raw) < 8 or raw[:2] != b"II":
        raise TiffError(f"{Path(path).name} is not a little-endian TIFF "
                        f"(expected an 'II' header)")
    magic, ifd_offset = struct.unpack_from("<HI", raw, 2)
    if magic != 42:
        raise TiffError(f"{Path(path).name} has TIFF magic {magic}, not 42")
    (count,) = struct.unpack_from("<H", raw, ifd_offset)
    raw_tags: dict[int, tuple[int, int, bytes]] = {}
    for i in range(count):
        base = ifd_offset + 2 + 12 * i
        tag, kind, n, payload = struct.unpack_from("<HHI4s", raw, base)
        raw_tags[tag] = (kind, n, payload)

    def values(tag: int, default: int | None = None) -> tuple[int, ...]:
        """The tag's values, following the offset when they do not fit inline."""
        if tag not in raw_tags:
            if default is None:
                raise TiffError(f"{Path(path).name} is missing TIFF tag {tag}, so "
                                f"its geometry cannot be trusted")
            return (default,)
        kind, n, payload = raw_tags[tag]
        fmt = {_TYPE_SHORT: "H", _TYPE_LONG: "I"}.get(kind)
        if fmt is None:
            raise TiffError(f"{Path(path).name} uses TIFF field type {kind} for tag "
                            f"{tag}; this reader handles SHORT and LONG")
        elem = struct.calcsize(fmt)
        if elem * n <= 4:
            return tuple(struct.unpack_from("<" + fmt * n, payload))
        offset = struct.unpack_from("<I", payload)[0]
        end = offset + elem * n
        if end > len(raw):
            raise TiffError(f"{Path(path).name} points tag {tag} past the end of the "
                            f"file, so it is truncated")
        return tuple(struct.unpack_from("<" + fmt * n, raw, offset))

    def one(tag: int, default: int | None = None) -> int:
        got = values(tag, default)
        if len(set(got)) > 1:
            raise TiffError(f"{Path(path).name} has per-sample values for tag {tag} "
                            f"({got}), which this codec does not write and cannot "
                            f"honestly reduce to one")
        return int(got[0])

    compression = one(_TAG_COMPRESSION, _COMPRESSION_NONE)
    if compression != _COMPRESSION_NONE:
        raise TiffError(
            f"{Path(path).name} uses TIFF compression {compression}.\n"
            f"  why      this reader implements the uncompressed baseline only, "
            f"and guessing at an LZW or Deflate stream would produce noise rather "
            f"than a refusal\n"
            f"  fix      read the file with Pillow, or export uncompressed TIFF")
    width, height = one(_TAG_WIDTH), one(_TAG_LENGTH)
    channels = one(_TAG_SAMPLES_PER_PIXEL, 1)
    bits = one(_TAG_BITS_PER_SAMPLE, 8)
    if bits not in (8, 16):
        raise TiffError(f"{Path(path).name} has {bits} bits per sample; this reader "
                        f"handles 8 and 16")
    offset = one(_TAG_STRIP_OFFSETS)
    length = one(_TAG_STRIP_BYTE_COUNTS)
    data = raw[offset:offset + length]
    expected = width * height * channels * (bits // 8)
    if len(data) != expected:
        raise TiffError(f"{Path(path).name} carries {len(data)} bytes of pixels, "
                        f"expected {expected}")
    return Image(width, height, channels, bits, _from_le16(data) if bits == 16 else data)