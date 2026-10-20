"""Filename templates: `{material}_{map}_{res}` and the rest of the token set.

PRESETS.md 2 lists the tokens and one rule that decides the whole design here:
**an unknown token is an error at load time, not a silent literal.** A template
with `{matrial}` in it must fail when the preset is read, because the alternative
is a directory full of files literally named `{matrial}_albedo_4k.png` -- which
looks like it worked until someone opens the folder.

The same rule applies to a *known* token with no value. `{version}` with
`pack.version` empty is not "render it as blank"; it is a request for a version
stamp the job does not have, and rendering `brick_albedo_4k_v.png` would be worse
than refusing.
"""

from __future__ import annotations

import re
from datetime import date

from ..core.errors import LimitError

__all__ = ["TOKENS", "check", "render", "res_token"]

#: Every token the schema defines. `scale` is the physical span in millimetres and
#: `project` is a free-text prefix; both are recorded in the manifest so a filename
#: can be traced back to the job that produced it.
TOKENS = ("material", "map", "res", "version", "renderer", "date", "category",
          "scale", "project")

_TOKEN_RE = re.compile(r"\{([a-z_]+)\}")

#: Templates may need a token the caller has no value for. These are the ones with
#: a defensible default, so a template can rely on them without the job setting
#: anything: the date is today, the renderer is the preset's target, the map is the
#: map being written. Everything else must be supplied or the render refuses.
_ALWAYS = ("date", "renderer", "map", "res")


def res_token(pixels: int) -> str:
    """`2048 -> "2k"`, `512 -> "512"` (PRESETS.md 2: `res` renders as 2k/4k/8k)."""
    if pixels >= 1024 and pixels % 1024 == 0:
        return f"{pixels // 1024}k"
    return str(pixels)


def check(template: str, where: str) -> None:
    """Refuse a template with an unknown token. Called when a preset loads."""
    known = set(TOKENS)
    for token in _TOKEN_RE.findall(template):
        if token not in known:
            close = ", ".join(sorted(known))
            raise LimitError(
                f"{where}: the filename template {template!r} uses the unknown "
                f"token {{{token}}}.\n"
                f"  why      an unknown token cannot be resolved, and rendering it "
                f"literally would put a file called '{{{token}}}' in the export "
                f"folder -- a mistake that looks like a success until someone opens "
                f"the folder\n"
                f"  fix      use one of: {close}")
    if "{" not in template:
        raise LimitError(
            f"{where}: the filename template {template!r} has no token at all.\n"
            f"  why      every map would be written to the same name across "
            f"resolutions and presets, and the last write would win silently\n"
            f"  fix      include at least {{map}}, e.g. "
            f"'{{material}}_{{map}}_{{res}}'")


def render(template: str, where: str, **values: object) -> str:
    """Resolve every token, or refuse naming the one without a value."""
    check(template, where)
    supplied = {k: v for k, v in values.items() if v not in (None, "")}
    for token in _TOKEN_RE.findall(template):
        if token in supplied:
            continue
        if token == "date":
            supplied["date"] = date.today().isoformat()
            continue
        if token in _ALWAYS:
            raise LimitError(
                f"{where}: cannot resolve {{{token}}} in {template!r}.\n"
                f"  why      the template asks for it and the job does not carry a "
                f"value for it, and an empty substitution would produce a filename "
                f"that looks deliberate\n"
                f"  fix      set the parameter that feeds it, or take the token out "
                f"of the template")

    def swap(match: re.Match[str]) -> str:
        token = match.group(1)
        if token not in supplied:
            raise LimitError(
                f"{where}: {{{token}}} has no value (it is a known token, so this "
                f"is a missing parameter rather than a typo).\n"
                f"  why      leaving it out would produce a filename with an empty "
                f"segment, which then looks like a naming decision\n"
                f"  fix      set the parameter that feeds {{{token}}}, or remove it "
                f"from the template")
        return str(supplied[token])

    name = _TOKEN_RE.sub(swap, template)
    # A template that resolves to an empty or separator-only name would be caught
    # by the filesystem as an error much later, and by then the run has "worked".
    if not name.strip(" _-."):
        raise LimitError(f"{where}: the template {template!r} resolved to "
                         f"{name!r}, which is not a filename")
    return name