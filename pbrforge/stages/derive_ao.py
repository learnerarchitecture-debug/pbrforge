"""derive_ao: a pure gate. When enabled=false (default), touches nothing."""

from ..core.context import PipelineContext
from ..stages.base import Stage, StageResult

__all__ = ["DeriveAO", "STAGE"]


class DeriveAO(Stage):
    name = "derive_ao"
    requires = ("derive_normal",)
    needs = ()
    produces = ()
    params_block = "ao"
    cacheable = True
    summary = "ambient occlusion derivation (gated off in this build)"

    def run(self, ctx: PipelineContext) -> StageResult:
        params = self.params(ctx)
        res = StageResult(self.name)
        if not params.enabled:
            res.note(ctx, "ao.enabled = false")
            return res
        raise RuntimeError("ao.enabled = true not implemented in this build")


STAGE = DeriveAO()
