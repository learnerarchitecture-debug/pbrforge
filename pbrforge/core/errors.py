"""Typed failures, so callers can catch a *kind* of problem rather than a message.

Every one of these is a condition the pipeline is expected to meet in normal use,
which is why they are separate types instead of `RuntimeError`:

  - `ColourSpaceError`   the user asked for something that would corrupt a map
  - `NonTileableError`   the honest refusal: this content has no repeat unit
  - `LimitError`         a clean refusal before the machine runs out of memory,
                         rather than after

A stage is allowed to raise these and let the orchestrator report them. It is not
allowed to print a warning and continue, because a pipeline that continues after
one of these has produced a plausible-looking wrong answer, which is worse than a
crash -- see docs/ARCHITECTURE.md 6.

These live in their own module because `stages/` and `core/` both need them, and
neither should have to import the other to raise the right type.
"""

from __future__ import annotations


class PbrForgeError(Exception):
    """Base class. Catching this catches everything the pipeline raises on purpose."""


class LimitError(PbrForgeError):
    """A clean refusal before exceeding a configured or physical limit.

    Used for `ingest.max_pixels` (16k x 16k), and for the pure-Python fallback in
    `core/image.py` when a job would take minutes of interpreter time that numpy
    would do in a second. The message always names the fix.
    """


class ColourSpaceError(LimitError):
    """A data map was about to be treated as colour, or colour as data.

    A subclass of `LimitError` because it is a refusal the caller can act on
    before anything is written: code that catches the general refusal type must
    not miss the one that corrupts a map silently.
    """


class NonTileableError(PbrForgeError):
    """The content has no repeat unit, so no seamless result can be honest.

    Raised with the recommendation in the message (usually: export it as a
    non-tiling macro map, or crop a region that does repeat). Marble, slabs and
    murals land here, and pretending otherwise produces a tile with a visible
    seam -- worse than refusing.
    """


class PngError(PbrForgeError):
    """A malformed or unsupported PNG."""


class TiffError(PbrForgeError):
    """A malformed or unsupported TIFF."""


class Cancelled(PbrForgeError):
    """The user stopped the run; partial output must not be reported as done."""
