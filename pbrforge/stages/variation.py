"""variation: a pure gate. When enabled=false (default), touches nothing."""

from ..core.context import PipelineContext
from ..stages.base import Stage, StageResult

__all__ = ["Variation", "STAGE"]


class Variation(Stage):
    name = "variation"
    requires = ("derive_metal",)
    needs = ()
    produces = ()
    params_block = "variation"
    cacheable = True
    summary = "detail variation (gated off in this build)"

    def run(self, ctx: PipelineContext) -> StageResult:
        params = self.params(ctx)
        res = StageResult(self.name)
        if not params.enabled:
            res.note(ctx, "variation.enabled = false")
            return res
        raise RuntimeError("variation.enabled = true not implemented in this build")


STAGE = Variation()
