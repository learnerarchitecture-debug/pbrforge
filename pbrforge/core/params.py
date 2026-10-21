"""One job's complete parameter set: the sole authority for every stage.

Rules this file enforces, because the orchestrator depends on them:

  - **Unknown keys are refused.** A typo in `material.json` must not be silently
    ignored; that is how a run produces the wrong map with no error.
  - **`digest()` is the cache key.** It covers every block and the global fields,
    so a changed parameter always changes the key.
  - **`to_dict()` round-trips.** JSON in, JSON out, same digest.
  - **Validation happens before the machine runs.** Every refusal names the
    problem and the fix, in the two-line form the CLI prints.

Defaults describe one sensible brick material: all stages on, AI layers off,
`generic-pbr` export.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from .errors import LimitError

#: The parameter blocks, in pipeline order. `Params.validate()` and the
#: serialiser both walk this tuple, so a new block is added in one place.
BLOCKS: tuple[str, ...] = (
    "ingest", "preprocess", "lattice", "seamless", "delight", "height",
    "normal", "ao", "rough", "metal", "variation", "matte", "upscale",
    "pack", "qc",
)


def _check_choice(value: Any, allowed: tuple[str, ...], where: str) -> None:
    if value not in allowed:
        raise LimitError(
            f"{where} is {value!r}, not one of {', '.join(allowed)}.\n"
            f"  why      the parameter selects a code path; an unknown value "
            f"would silently fall through to a default that may be wrong\n"
            f"  fix      use one of: {', '.join(allowed)}")


def _check_unit(value: float, where: str) -> None:
    if not 0.0 <= float(value) <= 1.0:
        raise LimitError(
            f"{where} must be in 0..1, got {value!r}.\n"
            f"  why      it is a fraction, and a value outside the range would "
            f"extrapolate instead of blending\n"
            f"  fix      use a number in 0..1")


# --------------------------------------------------------------------- ingest
@dataclass
class IngestParams:
    max_pixels: int = 16777216
    strip_metadata: bool = True
    detect_maps: bool = True

    def validate(self) -> None:
        if self.max_pixels < 256:
            raise LimitError(
                "ingest.max_pixels is below 256.\n"
                "  why      a texture smaller than 16x16 cannot carry a PBR set\n"
                "  fix      set a higher limit")


# ----------------------------------------------------------------- preprocess
@dataclass
class PreprocessParams:
    enabled: bool = True
    white_balance: str = "none"          # none / auto / gray_world / white_patch
    crop: str = "none"                   # none / content / square
    rectify: Any = None                  # None, or four [x, y] corners
    size: int = 0                        # 0 = keep the source size
    auto_levels: bool = False
    levels: tuple[float, float] = (0.0, 1.0)
    dejpeg: float = 0.0
    denoise: float = 0.0
    vignette_correct: float = 0.0

    def validate(self) -> None:
        _check_choice(self.white_balance,
                      ("none", "auto", "gray_world", "white_patch"),
                      "preprocess.white_balance")
        _check_choice(self.crop, ("none", "content", "square"),
                      "preprocess.crop")
        if not (0.0 <= self.levels[0] < self.levels[1] <= 1.0):
            raise LimitError(
                "preprocess.levels must be low < high in 0..1.\n"
                "  why      values outside 0..1 or reversed would invert or "
                "clamp the image\n"
                "  fix      use two numbers in 0..1, low < high")
        for name in ("dejpeg", "denoise", "vignette_correct"):
            _check_unit(getattr(self, name), f"preprocess.{name}")
        if self.size and self.size < 64:
            raise LimitError(
                "preprocess.size must be 0 (keep) or at least 64.\n"
                "  why      a smaller resize destroys the detail the rest of "
                "the pipeline measures\n"
                "  fix      use 0 or a size >= 64")
        if self.rectify is not None:
            corners = self.rectify
            if len(corners) != 4 or any(len(p) != 2 for p in corners):
                raise LimitError(
                    "preprocess.rectify must be None or four [x, y] corners.\n"
                    "  why      the rectifier needs a quadrilateral, clockwise "
                    "from the top left\n"
                    "  fix      pass [[x0,y0],[x1,y0],[x1,y1],[x0,y1]] in 0..1")


# -------------------------------------------------------------------- lattice
@dataclass
class LatticeParams:
    enabled: bool = True
    sensitivity: float = 0.7
    max_periods: tuple[int, int] = (1, 24)
    axis_lock: str = "auto"
    snap: bool = True

    def validate(self) -> None:
        _check_choice(self.axis_lock, ("auto", "x", "y", "none"),
                      "lattice.axis_lock")
        _check_unit(self.sensitivity, "lattice.sensitivity")
        if self.max_periods[0] < 1 or self.max_periods[1] > 64:
            raise LimitError(
                "lattice.max_periods must be 1..64.\n"
                "  why      periods outside this range cannot be reliably "
                "measured at the proxy resolution\n"
                "  fix      keep both numbers in 1..64")
        if self.max_periods[0] > self.max_periods[1]:
            raise LimitError(
                "lattice.max_periods has min > max.\n"
                "  why      the scan would have no range to search\n"
                "  fix      put the smaller number first")


# ------------------------------------------------------------------- seamless
@dataclass
class SeamlessParams:
    enabled: bool = True
    method: str = "auto"
    accept_score: int = 85
    detect_repeats: bool = True
    search_window: int = 96
    seam_band_px: int = 96
    structure_tolerance: float = 0.25

    def validate(self) -> None:
        _check_choice(self.method,
                      ("auto", "lattice", "offset", "mirror", "heal",
                       "spectral", "quilt", "ai"),
                      "seamless.method")
        if not 50 <= self.accept_score <= 100:
            raise LimitError(
                "seamless.accept_score must be 50..100.\n"
                "  why      a score below 50 is visibly not seamless; above "
                "100 is impossible\n"
                "  fix      use a number in 50..100")
        if self.seam_band_px != 96 and not 4 <= self.seam_band_px <= 256:
            raise LimitError(
                "seamless.seam_band_px must be 96 (auto) or 4..256.\n"
                "  why      the band width is in pixels\n"
                "  fix      use 96 or a number in 4..256")
        if self.search_window < 0 or self.search_window > 512:
            raise LimitError(
                "seamless.search_window must be 0..512.\n"
                "  why      the window is a trim per side; a larger one would "
                "throw away most of the source\n"
                "  fix      use a number in 0..512")
        _check_unit(self.structure_tolerance,
                    "seamless.structure_tolerance")


# -------------------------------------------------------------------- delight
@dataclass
class DelightParams:
    enabled: bool = True
    strength: float = 0.8
    preserve_micro: bool = True
    remove_specular: bool = False
    export_shading: bool = False

    def validate(self) -> None:
        _check_unit(self.strength, "delight.strength")


# --------------------------------------------------------------------- height
@dataclass
class HeightParams:
    enabled: bool = True
    source: str = "delight"         # delight / original / external
    #: Bright pixels are high by default. Brick is the common exception: the
    #: mortar is often lighter than the brick, but physically recessed, so the
    #: brick category flips this. Exposed in the UI as "height polarity".
    invert: bool = False
    smooth: float = 0.0
    detail_boost: float = 0.0
    amplitude_mm: float = 10.0
    midlevel: float = 0.5
    bit_depth: int = 16
    size_mm: tuple[float, float] | None = None

    def validate(self) -> None:
        _check_choice(self.source, ("delight", "original", "external"),
                      "height.source")
        if self.bit_depth not in (8, 16):
            raise LimitError(
                "height.bit_depth must be 8 or 16.\n"
                "  why      the writers quantise to those depths\n"
                "  fix      use 8 or 16")
        if self.amplitude_mm <= 0:
            raise LimitError(
                "height.amplitude_mm must be positive.\n"
                "  why      displacement amplitude is a length\n"
                "  fix      use a positive number")
        _check_unit(self.smooth, "height.smooth")
        _check_unit(self.midlevel, "height.midlevel")
        if not 0.0 <= self.detail_boost <= 2.0:
            raise LimitError(
                "height.detail_boost must be in 0..2.\n"
                "  why      it multiplies the high-frequency band\n"
                "  fix      use a number in 0..2")


# --------------------------------------------------------------------- normal
@dataclass
class NormalParams:
    enabled: bool = True
    strength: float = 1.0
    orientation: str = "opengl"
    bit_depth: int = 16
    smooth_before: float = 0.0
    renormalise_after_resize: bool = True
    clamp_z: float = 0.0
    filter: str = "sobel"

    def validate(self) -> None:
        _check_choice(self.orientation, ("opengl", "directx"),
                      "normal.orientation")
        _check_choice(self.filter, ("central", "scharr", "sobel"),
                      "normal.filter")
        if self.bit_depth not in (8, 16):
            raise LimitError(
                "normal.bit_depth must be 8 or 16.\n"
                "  why      the writers quantise to those depths\n"
                "  fix      use 8 or 16")
        _check_unit(self.smooth_before, "normal.smooth_before")
        if not 0.0 <= self.clamp_z < 1.0:
            raise LimitError(
                "normal.clamp_z must be in 0..1 (exclusive at 1).\n"
                "  why      it is the minimum z before renormalising; 1 would "
                "flatten every normal\n"
                "  fix      use 0 for no clamp, or a number in 0..1")
        if not 0.0 <= self.strength <= 4.0:
            raise LimitError(
                "normal.strength must be in 0..4.\n"
                "  why      it scales the gradient before normalising\n"
                "  fix      use a number in 0..4")
        if not self.renormalise_after_resize:
            raise LimitError(
                "normal.renormalise_after_resize = false is refused.\n"
                "  why      resampling a tangent-space normal interpolates it "
                "off the unit sphere, so the lighting comes out dimmer and "
                "skewed -- silently\n"
                "  fix      leave renormalise_after_resize = true; the pass is "
                "cheap and it is the difference between a normal map and a "
                "colour map that looks like one")


# ------------------------------------------------------------------------- ao
@dataclass
class AOParams:
    enabled: bool = True
    strength: float = 0.6
    radius_px: int = 32
    multiscale: bool = True
    invert: bool = False
    curvature_gain: float = 12.0
    export_cavity: bool = False
    export_curvature: bool = False

    def validate(self) -> None:
        if self.radius_px < 1:
            raise LimitError(
                "ao.radius_px must be >= 1.\n"
                "  why      a zero-radius occlusion is an identity\n"
                "  fix      use a positive integer")
        _check_unit(self.strength, "ao.strength")
        if self.curvature_gain <= 0:
            raise LimitError(
                "ao.curvature_gain must be positive.\n"
                "  why      it scales the curvature response\n"
                "  fix      use a positive number")


# ---------------------------------------------------------------------- rough
@dataclass
class RoughParams:
    enabled: bool = True
    algorithm: str = "height"   # height / hybrid / cavity / specular / flat
    base: float = 0.55
    remap: tuple[float, float] = (0.1, 0.9)
    cavity_influence: float = 1.0
    specular_influence: float = 0.5
    variation: float = 0.25
    macro_variation: float = 0.0
    clamp: tuple[float, float] = (0.05, 0.95)
    output_mode: str = "roughness"

    def validate(self) -> None:
        _check_choice(self.algorithm,
                      ("height", "hybrid", "cavity", "specular", "flat"),
                      "rough.algorithm")
        _check_choice(self.output_mode, ("roughness", "glossiness"),
                      "rough.output_mode")
        _check_unit(self.base, "rough.base")
        for name in ("cavity_influence", "specular_influence", "variation",
                     "macro_variation"):
            _check_unit(getattr(self, name), f"rough.{name}")
        low, high = self.clamp
        if not 0.0 < low < high < 1.0:
            raise LimitError(
                f"rough.clamp is {self.clamp!r}; it must be strictly inside "
                f"0..1 with low < high.\n"
                f"  why      a clamp of exactly 0..1 is no clamp at all, and "
                f"a reversed one inverts the map -- the refusal is the point "
                f"of naming the range\n"
                f"  fix      use something like (0.05, 0.95)")
        low, high = self.remap
        if not 0.0 <= low < high <= 1.0:
            raise LimitError(
                "rough.remap must be low < high in 0..1.\n"
                "  why      it maps the measured range onto the output\n"
                "  fix      use two numbers in 0..1, low < high")


# ---------------------------------------------------------------------- metal
@dataclass
class MetalParams:
    enabled: bool = True
    mode: str = "classify"       # classify / zero / one / masked
    value: float = 0.0
    mask: str | None = None
    confidence_min: float = 0.6

    def validate(self) -> None:
        _check_choice(self.mode, ("classify", "zero", "one", "masked"),
                      "metal.mode")
        _check_unit(self.confidence_min, "metal.confidence_min")
        if self.mode in ("zero", "one") and self.value != (
                0.0 if self.mode == "zero" else 1.0):
            raise LimitError(
                f"metal.mode = '{self.mode}' with metal.value = {self.value}.\n"
                f"  why      this mode does not read a value -- the expected "
                f"value is {0.0 if self.mode == 'zero' else 1.0} -- so the "
                f"number would be silently ignored\n"
                f"  fix      set metal.value to "
                f"{0.0 if self.mode == 'zero' else 1.0}, or use mode "
                f"'classify' and let the evidence decide")
        if self.mode == "masked" and not self.mask:
            raise LimitError(
                "metal.mode = 'masked' needs metal.mask.\n"
                "  why      the mask is the whole difference between guessing "
                "at a mixed material and being told which pixels are metal\n"
                "  fix      set metal.mask to a greyscale PNG (0 = dielectric, "
                "1 = metal, at the same pixel size as the albedo), or use "
                "mode 'classify'")
        if self.mode == "classify" and self.value != 0.0:
            raise LimitError(
                f"metal.mode = 'classify' with metal.value = {self.value}.\n"
                f"  why      this mode does not read a value -- the expected "
                f"value is 0.0 -- so the number would be silently ignored\n"
                f"  fix      set metal.value to 0.0, or use mode 'zero'/'one' "
                f"for a constant decision")


# ------------------------------------------------------------------ variation
@dataclass
class VariationParams:
    enabled: bool = True
    detail_split: float = 0.5
    preserve_rgb: bool = True
    #: Macro-scale lighting variation: an independent, deliberately NON-tiling
    #: field. Kept separate from the tile so a renderer can use it as a large
    #: breakup layer without destroying the seamless repeat.
    macro_strength: float = 0.25
    macro_hue_shift: float = 0.1
    roughness_nudge: float = 0.1
    output_macro_map: bool = True

    def validate(self) -> None:
        _check_unit(self.detail_split, "variation.detail_split")
        _check_unit(self.macro_strength, "variation.macro_strength")
        _check_unit(self.macro_hue_shift, "variation.macro_hue_shift")
        _check_unit(self.roughness_nudge, "variation.roughness_nudge")


# ---------------------------------------------------------------------- matte
@dataclass
class MatteParams:
    enabled: bool = True
    mode: str = "closed_form"     # closed_form / ml
    refinement: int = 0

    def validate(self) -> None:
        _check_choice(self.mode, ("closed_form", "ml"), "matte.mode")
        if self.mode == "ml":
            raise LimitError(
                "matte.mode = 'ml' requests an AI matting model, and this "
                "build ships none.\n"
                "  why      the AI layer needs model weights with their own "
                "licences and several GB of VRAM\n"
                "  fix      see docs/AI_FEATURES.md for the unlock conditions, "
                "or use 'closed_form'")


# -------------------------------------------------------------------- upscale
@dataclass
class UpscaleParams:
    enabled: bool = False
    scale: int = 2
    model: str = "tiling-realplksr-v2"
    tiling_aware: bool = True
    detail_guard: float = 0.5
    seed: int = 1234

    def validate(self) -> None:
        if self.enabled:
            raise LimitError(
                "upscale.enabled = true is refused.\n"
                "  why      this build ships no AI model weights; the model "
                "name names a weights file that does not exist here\n"
                "  fix      see docs/AI_FEATURES.md for the unlock conditions, "
                "or keep enabled = false")
        if self.scale not in (2, 4):
            raise LimitError(
                "upscale.scale must be 2 or 4.\n"
                "  why      those are the model's native factors\n"
                "  fix      use 2 or 4")
        _check_unit(self.detail_guard, "upscale.detail_guard")


# ----------------------------------------------------------------------- pack
@dataclass
class PackParams:
    preset: str = "generic-pbr"
    resolutions: tuple[int, ...] = (4096,)
    channel_order: str | None = None
    normal_orientation: str | None = None
    roughness_mode: str | None = None
    filename_template: str = "{material}_{map}_{res}"
    realtime_compression: str = "off"
    write_sidecar: bool = True
    write_handoff: bool = True
    material_name: str = ""
    category: str = "unknown"
    version: str = ""

    def validate(self) -> None:
        if self.realtime_compression != "off":
            raise LimitError(
                "pack.realtime_compression is not 'off'.\n"
                "  why      KTX2/BasisU and DDS compression need external tools "
                "(basisu, texconv) that this build does not ship\n"
                "  fix      install those tools and this becomes possible, or "
                "leave it 'off'")
        if not self.resolutions:
            raise LimitError(
                "pack.resolutions is empty.\n"
                "  why      the export would write no files\n"
                "  fix      list at least one resolution, e.g. (4096,)")
        for res in self.resolutions:
            if res < 16:
                raise LimitError(
                    f"pack.resolutions contains {res}.\n"
                    f"  why      a texture below 16 px cannot carry a PBR set\n"
                    f"  fix      use resolutions >= 16")
        if self.channel_order not in (None, "orm", "separate"):
            raise LimitError(
                f"pack.channel_order is {self.channel_order!r}.\n"
                f"  why      only 'orm' (packed) and 'separate' are defined\n"
                f"  fix      use null, 'orm', or 'separate'")
        if self.normal_orientation not in (None, "opengl", "directx"):
            raise LimitError(
                f"pack.normal_orientation is {self.normal_orientation!r}.\n"
                f"  why      a normal map's green channel has two conventions\n"
                f"  fix      use null, 'opengl', or 'directx'")
        if self.roughness_mode not in (None, "roughness", "glossiness"):
            raise LimitError(
                f"pack.roughness_mode is {self.roughness_mode!r}.\n"
                f"  why      roughness and glossiness are inverse measures\n"
                f"  fix      use null, 'roughness', or 'glossiness'")


# ------------------------------------------------------------------------- qc
@dataclass
class QCParams:
    enabled: bool = True
    seam_warn: int = 75
    seam_fail: int = 70
    structure_auto_gate: float = 0.97
    structure_spectral_gate: float = 0.85
    albedo_mean_fail: tuple[float, float] = (0.15, 0.75)
    clip_black_warn: float = 0.001
    clip_white_warn: float = 0.005
    normal_blue_min: float = 0.5
    normal_xy_max: float = 0.35
    normal_unit_max: float = 0.05
    rough_std_warn: float = 0.02
    rough_mean_pass: tuple[float, float] = (0.15, 0.92)
    rough_mean_warn: tuple[float, float] = (0.08, 0.97)
    rough_entropy_pass: float = 4.0

    def validate(self) -> None:
        if not 0 <= self.seam_fail <= self.seam_warn <= 100:
            raise LimitError(
                "qc.seam_fail must be <= seam_warn, both in 0..100.\n"
                "  why      the warn band must sit above the fail floor\n"
                "  fix      order them correctly")


#: Fields that are tuples on the dataclass but arrive as JSON lists.
_TUPLE_FIELDS: dict[str, tuple[str, ...]] = {
    "height": ("size_mm",),
    "lattice": ("max_periods",),
    "preprocess": ("levels",),
    "rough": ("clamp", "remap"),
    "qc": ("albedo_mean_fail", "rough_mean_pass", "rough_mean_warn"),
}

_GLOBAL_FIELDS = ("material", "category", "seed", "size_mm")


@dataclass
class Params:
    """One job's complete parameter set. Defaults = one sensible brick material."""

    material: str = "material"
    category: str = "unknown"
    seed: int = 1234
    size_mm: tuple[float, float] | None = None

    ingest: IngestParams = field(default_factory=IngestParams)
    preprocess: PreprocessParams = field(default_factory=PreprocessParams)
    lattice: LatticeParams = field(default_factory=LatticeParams)
    seamless: SeamlessParams = field(default_factory=SeamlessParams)
    delight: DelightParams = field(default_factory=DelightParams)
    height: HeightParams = field(default_factory=HeightParams)
    normal: NormalParams = field(default_factory=NormalParams)
    ao: AOParams = field(default_factory=AOParams)
    rough: RoughParams = field(default_factory=RoughParams)
    metal: MetalParams = field(default_factory=MetalParams)
    variation: VariationParams = field(default_factory=VariationParams)
    matte: MatteParams = field(default_factory=MatteParams)
    upscale: UpscaleParams = field(default_factory=UpscaleParams)
    pack: PackParams = field(default_factory=PackParams)
    qc: QCParams = field(default_factory=QCParams)

    # ------------------------------------------------------------- accessors
    def block(self, name: str) -> Any:
        if name not in BLOCKS:
            raise LimitError(
                f"unknown parameter block {name!r}.\n"
                f"  why      a stage asked for a block that does not exist\n"
                f"  fix      use one of: {', '.join(BLOCKS)}")
        return getattr(self, name)

    # -------------------------------------------------------------- category
    def apply_category(self, name: str) -> "Params":
        """Apply a material-class overlay. Returns self, so it chains."""
        key = (name or "unknown").strip().lower()
        if key in ("brick", "brick_wall", "masonry"):
            self.category = "brick"
            self.height.source = "delight"
            self.height.invert = True
            self.height.detail_boost = 0.5
            self.rough.base = 0.65
            self.ao.strength = 0.7
            self.seamless.method = "auto"
            self.metal.mode = "zero"
            self.metal.value = 0.0
        elif key in ("marble", "stone", "tile"):
            self.category = "marble"
            self.lattice.enabled = False
            self.height.source = "original"
            self.rough.base = 0.3
            self.ao.strength = 0.4
            self.metal.mode = "zero"
            self.metal.value = 0.0
        elif key in ("unknown", "generic", ""):
            self.category = "unknown"
        else:
            raise LimitError(
                f"unknown material category {name!r}.\n"
                f"  why      a category sets stage defaults; an unknown one "
                f"would leave the run half-configured\n"
                f"  fix      use 'brick', 'marble', or 'unknown'")
        return self

    # ---------------------------------------------------------- serialisation
    def to_dict(self) -> dict[str, Any]:
        """A JSON-stable dict. Tuples become lists; None stays None."""
        out: dict[str, Any] = {
            "material": self.material,
            "category": self.category,
            "seed": self.seed,
            "size_mm": list(self.size_mm) if self.size_mm else None,
        }
        for name in BLOCKS:
            block = getattr(self, name)
            out[name] = {k: _plain(v) for k, v in vars(block).items()}
        return out

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Params":
        """Build from a dict, refusing anything the schema does not know."""
        if not isinstance(data, dict):
            raise LimitError(
                f"parameters must be a JSON object, got {type(data).__name__}.\n"
                f"  why      the loader maps keys onto blocks\n"
                f"  fix      pass an object, e.g. {{'material': 'x'}}")
        unknown = sorted(set(data) - set(_GLOBAL_FIELDS) - set(BLOCKS))
        if unknown:
            raise LimitError(
                f"unknown top-level parameter(s): {', '.join(unknown)}.\n"
                f"  why      a typo would otherwise be ignored and the run "
                f"would use a default the user did not ask for\n"
                f"  fix      use only: {', '.join(_GLOBAL_FIELDS + BLOCKS)}")

        obj = cls()
        for key, value in data.items():
            if key not in BLOCKS:
                if key == "size_mm":
                    setattr(obj, key, tuple(value) if value else None)
                else:
                    setattr(obj, key, value)
                continue
            block = getattr(obj, key)
            if not isinstance(value, dict):
                raise LimitError(
                    f"parameter block {key!r} must be an object, got "
                    f"{type(value).__name__}.\n"
                    f"  why      a block is a set of named fields\n"
                    f"  fix      pass {{...}} for {key}")
            fields = set(vars(block))
            bad = sorted(set(value) - fields)
            if bad:
                raise LimitError(
                    f"unknown field(s) in {key}: {', '.join(bad)}.\n"
                    f"  why      a typo would otherwise be ignored and the "
                    f"run would use a default the user did not ask for\n"
                    f"  fix      use one of: {', '.join(sorted(fields))}")
            tuples = _TUPLE_FIELDS.get(key, ())
            for field_name, field_value in value.items():
                if field_name in tuples and field_value is not None:
                    field_value = tuple(field_value)
                setattr(block, field_name, field_value)
        return obj

    # ----------------------------------------------------------------- digest
    def digest(self, *extra: object) -> str:
        """Stable 16-hex cache key over every block plus `extra`."""
        payload = {
            "global": {
                "material": self.material,
                "category": self.category,
                "seed": self.seed,
                "size_mm": list(self.size_mm) if self.size_mm else None,
            },
            "blocks": {
                name: {k: _plain(v) for k, v in vars(getattr(self, name)).items()}
                for name in BLOCKS
            },
            "extra": [_plain(e) for e in extra],
        }
        text = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]

    # --------------------------------------------------------------- validate
    def validate(self) -> None:
        for name in BLOCKS:
            getattr(self, name).validate()


def _plain(value: Any) -> Any:
    if isinstance(value, tuple):
        return [_plain(v) for v in value]
    if isinstance(value, list):
        return [_plain(v) for v in value]
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    return value
