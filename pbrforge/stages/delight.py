"""delight: a pure gate. When enabled=false (default), touches nothing."""

from ..core.context import PipelineContext
from ..stages.base import Stage, StageResult

__all__ = ["Delight", "STAGE"]


class Delight(Stage):
    name = "delight"
    requires = ("seamless",)
    needs = ()
    produces = ()
    params_block = "delight"
    cacheable = True
    summary = "de-lighting (gated off in this build)"

    def run(self, ctx: PipelineContext) -> StageResult:
        params = self.params(ctx)
        res = StageResult(self.name)
        if not params.enabled:
            res.note(ctx, "delight.enabled = false")
            return res
        raise RuntimeError("delight.enabled = true not implemented in this build")


STAGE = Delight()
