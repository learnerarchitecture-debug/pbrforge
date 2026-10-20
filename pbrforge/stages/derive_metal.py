"""derive_metal: a pure gate. When enabled=false (default), touches nothing."""

from ..core.context import PipelineContext
from ..stages.base import Stage, StageResult

__all__ = ["DeriveMetal", "STAGE"]


class DeriveMetal(Stage):
    name = "derive_metal"
    requires = ("derive_rough",)
    needs = ()
    produces = ()
    params_block = "metal"
    cacheable = True
    summary = "metallic map derivation (gated off in this build)"

    def run(self, ctx: PipelineContext) -> StageResult:
        params = self.params(ctx)
        res = StageResult(self.name)
        if not params.enabled:
            res.note(ctx, "metal.enabled = false")
            return res
        raise RuntimeError("metal.enabled = true not implemented in this build")


STAGE = DeriveMetal()
