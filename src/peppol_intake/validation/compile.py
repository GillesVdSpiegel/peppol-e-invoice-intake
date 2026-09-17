"""Compile ISO Schematron rule sets into SVRL-emitting XSLT.

Upstream ships Schematron source, not compiled stylesheets, so we run the
standard three-stage ISO skeleton pipeline ourselves:

    .sch -> iso_dsdl_include -> iso_abstract_expand -> iso_svrl_for_xslt2 -> .xslt

`allow-foreign` is required: the Peppol rule set defines xsl:function elements
(u:mod97-0208 for Belgian enterprise numbers, u:gln, u:checkPIVA, ...) inline in
the Schematron, and the skeleton drops foreign elements without it.

Compiled output is cached on disk and reused unless the source is newer.
"""

from __future__ import annotations

from pathlib import Path

from .artefacts import COMPILED_DIR, SKELETON_DIR, RuleSet, require_artefacts
from .saxon import processor, xslt_processor

_STAGES = ("iso_dsdl_include.xsl", "iso_abstract_expand.xsl", "iso_svrl_for_xslt2.xsl")


def _is_stale(source: Path, target: Path) -> bool:
    return not target.exists() or target.stat().st_mtime < source.stat().st_mtime


def compile_schematron(sch: Path, target: Path, *, force: bool = False) -> Path:
    if not force and not _is_stale(sch, target):
        return target

    require_artefacts()
    proc = processor()
    xp = xslt_processor()

    current = proc.parse_xml(xml_file_name=str(sch))
    for stage_name in _STAGES:
        executable = xp.compile_stylesheet(stylesheet_file=str(SKELETON_DIR / stage_name))
        if stage_name == "iso_svrl_for_xslt2.xsl":
            executable.set_parameter("allow-foreign", proc.make_string_value("true"))
        output = executable.transform_to_string(xdm_node=current)
        if output is None:
            raise RuntimeError(
                f"Schematron compile stage {stage_name} produced no output for {sch}"
            )
        current = proc.parse_xml(xml_text=output)

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(output, encoding="utf-8")
    return target


def ensure_compiled(rule_sets: tuple[RuleSet, ...], *, force: bool = False) -> list[Path]:
    COMPILED_DIR.mkdir(parents=True, exist_ok=True)
    return [compile_schematron(rs.sch, rs.compiled, force=force) for rs in rule_sets]
