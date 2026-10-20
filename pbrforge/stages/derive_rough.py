"""derive_rough: a pure gate. When enabled=false (default), touches nothing."""

from ..core.context import PipelineContext
from ..stages.base import Stage, StageResult

__all__ = ["DeriveRough", "STAGE"]


class DeriveRough(Stage):
    name = "derive_rough"
    requires = ("derive_ao",)
    needs = ()
    produces = ()
    params_block = "rough"
    cacheable = True
    summary = "roughness map derivation (gated off in this build)"

    def run(self, ctx: PipelineContext) -> StageResult:
        params = self.params(ctx)
        res = StageResult(self.name)
        if not params.enabled:
            res.note(ctx, "rough.enabled = false")
            return res
        raise RuntimeError("rough.enabled = true not implemented in this build")


STAGE = DeriveRough()
