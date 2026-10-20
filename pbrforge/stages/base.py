"""Stage protocol and result types.

Every stage in the pipeline inherits from `Stage` and implements `run()`. The
declarations (`requires`, `needs`, `produces`, `params_block`, `cacheable`) are
read by the orchestrator to build the DAG and the cache key.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..core.context import PipelineContext

__all__ = ["Stage", "StageResult"]


@dataclass
class StageResult:
    """What a stage reports back. Metrics land in `material.json`."""

    name: str
    metrics: dict[str, float] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    seconds: float = 0.0
    cached: bool = False
    #: Free-form extras for the UI (a bounding box, the detected period, a method
    #: name). Not put in `metrics` because they are not all numbers.
    detail: dict[str, Any] = field(default_factory=dict)

    def note(self, ctx: PipelineContext, message: str) -> None:
        self.notes.append(message)
        ctx.note(message)

    def warn(self, ctx: PipelineContext, message: str) -> None:
        self.warnings.append(message)
        ctx.warn(message)

    def metric(self, ctx: PipelineContext, name: str, value: float) -> None:
        self.metrics[name] = value
        ctx.metrics[name] = value


class Stage:
    """Base class. Subclasses set the declarations and implement `run`."""

    name: str = ""
    requires: tuple[str, ...] = ()
    needs: tuple[str, ...] = ()
    produces: tuple[str, ...] = ()
    #: Map kinds the stage writes into `ctx.inputs` (decoded source material)
    #: rather than `ctx.maps` (the working set). Only `ingest` uses this, and it
    #: overrides `kinds_produced` below, because what it produces depends on what
    #: the user dropped in.
    produces_inputs: tuple[str, ...] = ()
    params_block: str = ""
    cacheable: bool = True
    #: One line for `pbrforge stages --list` and the UI"s stage list.
    summary: str = ""

    # ------------------------------------------------------------------ utils
    def params(self, ctx: PipelineContext) -> Any:
        """This stage"s parameter block, or `None` if it has none."""
        if not self.params_block:
            return None
        return ctx.params.block(self.params_block)

    def enabled(self, ctx: PipelineContext) -> bool:
        """False means "skip cleanly", not "fail". Read from the params block.

        A stage that is off (`alphamatte`, `upscale`) still appears in the DAG and
        still reports a result, saying it is off. That is what keeps the stage list
        stable in the UI: a stage never disappears, it becomes a no-op.
        """
        block = self.params(ctx)
        return bool(getattr(block, "enabled", True))

    def kinds_produced(self, where: str, ctx: PipelineContext) -> tuple[str, ...]:
        """Which map kinds this stage actually produced, for the cache.

        The default reads the declaration. `ingest` overrides it because a drop can
        contain any subset of a PBR set, so the answer is only known after the stage
        has run -- and the cache stores whatever it says.
        """
        return self.produces_inputs if where == "inputs" else self.produces

    def upstream(self, ctx: PipelineContext, keys: dict[str, str]) -> tuple[str, ...]:
        """Fingerprints of this stage"s inputs, for the cache key.

        The default is the Merkle chain: the cache keys of the stages this one
        requires. `ingest` overrides it because it has no upstream stage -- it
        depends on the bytes of the source files.
        """
        return tuple(keys[r] for r in self.requires if r in keys)

    # ------------------------------------------------------------------- main
    def run(self, ctx: PipelineContext) -> StageResult:      # pragma: no cover
        raise NotImplementedError(f"{type(self).__name__}.run() is not implemented")

    def __repr__(self) -> str:
        return f"<stage {self.name}>"
