"""derive_normal: a pure gate. When enabled=false (default), touches nothing."""

from ..core.context import PipelineContext
from ..stages.base import Stage, StageResult

__all__ = ["DeriveNormal", "STAGE"]


class DeriveNormal(Stage):
    name = "derive_normal"
    requires = ("derive_height",)
    needs = ()
    produces = ()
    params_block = "normal"
    cacheable = True
    summary = "normal map derivation (gated off in this build)"

    def run(self, ctx: PipelineContext) -> StageResult:
        params = self.params(ctx)
        res = StageResult(self.name)
        if not params.enabled:
            res.note(ctx, "normal.enabled = false")
            return res
        raise RuntimeError("normal.enabled = true not implemented in this build")


STAGE = DeriveNormal()
