"""Font inventory introspection: which fonts a page declares, and whether
each carries a ToUnicode CMap.

Verified on docs/Newspaper.pdf: almost no fonts carry ToUnicode (extraction
relies on WinAnsi/Differences encoding instead), but it is NOT universal -
pages 2 and 11 each have a couple of fonts that DO carry one. Do not assume
page-1 behavior generalizes; this inventory is recorded per page so any
future page/edition that changes prepress toolchains is visible in the data
rather than silently assumed away.
"""
from __future__ import annotations

from hindu_extract.models import FontInfo


def _resolve(obj):
    return obj.resolve() if hasattr(obj, "resolve") else obj


def get_font_inventory(page, chars: list[dict]) -> tuple[FontInfo, ...]:
    char_counts: dict[str, int] = {}
    for c in chars:
        char_counts[c["fontname"]] = char_counts.get(c["fontname"], 0) + 1

    tounicode_by_base: dict[str, bool] = {}
    try:
        resources = _resolve(page.page_obj.attrs.get("Resources", {}))
        fonts = _resolve(resources.get("Font", {})) if resources else {}
        for _fname, fref in (fonts or {}).items():
            try:
                fobj = _resolve(fref)
                base = str(fobj.get("BaseFont", "")).lstrip("/")
                tounicode_by_base[base] = "ToUnicode" in fobj
            except Exception:
                continue
    except Exception:
        pass

    infos = []
    for font_name, count in sorted(char_counts.items()):
        has_tu = tounicode_by_base.get(font_name, False)
        infos.append(FontInfo(font_name=font_name, char_count=count, has_tounicode=has_tu))
    return tuple(infos)
