"""derive_height: a pure gate. When enabled=false (default), touches nothing."""

from ..core.context import PipelineContext
from ..stages.base import Stage, StageResult

__all__ = ["DeriveHeight", "STAGE"]


class DeriveHeight(Stage):
    name = "derive_height"
    requires = ("delight",)
    needs = ()
    produces = ()
    params_block = "height"
    cacheable = True
    summary = "height map derivation (gated off in this build)"

    def run(self, ctx: PipelineContext) -> StageResult:
        params = self.params(ctx)
        res = StageResult(self.name)
        if not params.enabled:
            res.note(ctx, "height.enabled = false")
            return res
        raise RuntimeError("height.enabled = true not implemented in this build")


STAGE = DeriveHeight()
