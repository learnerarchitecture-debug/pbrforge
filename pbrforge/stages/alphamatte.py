"""alphamatte: a pure gate. When enabled=false (default), touches nothing."""

from ..core.context import PipelineContext
from ..stages.base import Stage, StageResult

__all__ = ["AlphaMatte", "STAGE"]


class AlphaMatte(Stage):
    name = "alphamatte"
    requires = ("variation",)
    needs = ()
    produces = ()
    params_block = "matte"
    cacheable = True
    summary = "alpha matting (gated off in this build)"

    def run(self, ctx: PipelineContext) -> StageResult:
        params = self.params(ctx)
        res = StageResult(self.name)
        if not params.enabled:
            res.note(ctx, "matte.enabled = false: this build ships no matting model; "
                          "see docs/AI_FEATURES.md for the unlock conditions")
            return res
        raise RuntimeError("matte.enabled = true should have been refused by params.validate()")


STAGE = AlphaMatte()
