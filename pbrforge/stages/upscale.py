"""`upscale`: a declared boundary, not a hidden fallback.

This build ships no AI weights. The parameter validator already refuses
``upscale.enabled = true`` with the reason and the fix; this stage makes the
disabled path explicit in the run record and never resizes behind the user's
back. A plain bilinear upscale would be a different, worse product than the
tiling-aware model the parameters describe, so it is not offered as a substitute.
"""

from __future__ import annotations

from ..core.context import PipelineContext
from ..stages.base import Stage, StageResult

__all__ = ["Upscale", "STAGE"]


class Upscale(Stage):
    name = "upscale"
    requires = ("alphamatte",)
    needs = ()
    produces = ()
    params_block = "upscale"
    cacheable = True
    summary = "AI upscale is disabled in this build"

    def run(self, ctx: PipelineContext) -> StageResult:
        params = self.params(ctx)
        params.validate()  # refuses enabled=true, by name, before any work
        res = StageResult(self.name)
        res.note(ctx, "upscale.enabled = false: this build ships no model weights; "
                      "the delivered size is the working size. See "
                      "docs/AI_FEATURES.md for the unlock conditions.")
        res.metric(ctx, "upscale.scale", 1.0)
        res.detail["line"] = "no upscale (model weights absent)"
        return res


STAGE = Upscale()
