"""upscale: a pure gate. When enabled=false (default), touches nothing."""

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
    summary = "AI upscale (gated off in this build)"

    def run(self, ctx: PipelineContext) -> StageResult:
        params = self.params(ctx)
        res = StageResult(self.name)
        if not params.enabled:
            res.note(ctx, "upscale.enabled = false: this build ships no model weights; "
                          "see docs/AI_FEATURES.md for the unlock conditions")
            return res
        # enabled path is refused by params.validate()
        raise RuntimeError("upscale.enabled = true should have been refused by params.validate()")


STAGE = Upscale()
