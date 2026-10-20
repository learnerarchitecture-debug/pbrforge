"""preprocess: a pure gate. When enabled=false (default), touches nothing."""

from ..core.context import PipelineContext
from ..stages.base import Stage, StageResult

__all__ = ["Preprocess", "STAGE"]


class Preprocess(Stage):
    name = "preprocess"
    requires = ("ingest",)
    needs = ()
    produces = ()
    params_block = "preprocess"
    cacheable = True
    summary = "pre-processing (gated off in this build)"

    def run(self, ctx: PipelineContext) -> StageResult:
        params = self.params(ctx)
        res = StageResult(self.name)
        if not params.enabled:
            res.note(ctx, "preprocess.enabled = false")
            return res
        raise RuntimeError("preprocess.enabled = true not implemented in this build")


STAGE = Preprocess()
