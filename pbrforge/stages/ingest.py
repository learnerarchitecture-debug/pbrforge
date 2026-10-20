"""ingest: a pure gate. When enabled=false (default), touches nothing."""

from ..core.context import PipelineContext
from ..stages.base import Stage, StageResult

__all__ = ["Ingest", "STAGE"]


class Ingest(Stage):
    name = "ingest"
    requires = ()
    needs = ()
    produces = ()
    produces_inputs = ("albedo",)
    params_block = "ingest"
    cacheable = True
    summary = "input ingestion (gated off in this build)"

    def run(self, ctx: PipelineContext) -> StageResult:
        params = self.params(ctx)
        res = StageResult(self.name)
        if not params.enabled:
            res.note(ctx, "ingest.enabled = false")
            return res
        raise RuntimeError("ingest.enabled = true not implemented in this build")


STAGE = Ingest()
