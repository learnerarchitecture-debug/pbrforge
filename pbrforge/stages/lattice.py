"""lattice: a pure gate. When enabled=false (default), touches nothing."""

from ..core.context import PipelineContext
from ..stages.base import Stage, StageResult

__all__ = ["Lattice", "STAGE"]


class Lattice(Stage):
    name = "lattice"
    requires = ("preprocess",)
    needs = ()
    produces = ()
    params_block = "lattice"
    cacheable = True
    summary = "lattice detection (gated off in this build)"

    def run(self, ctx: PipelineContext) -> StageResult:
        params = self.params(ctx)
        res = StageResult(self.name)
        if not params.enabled:
            res.note(ctx, "lattice.enabled = false")
            return res
        raise RuntimeError("lattice.enabled = true not implemented in this build")


STAGE = Lattice()
