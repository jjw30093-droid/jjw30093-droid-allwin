"""Pure known-marker pagination inspection for saved FotMob schedules."""

from __future__ import annotations

from typing import Any


_CONTINUATION_KEYS = {
    "next",
    "nextpage",
    "nexturl",
    "cursor",
    "nextcursor",
    "previousfixturesurl",
    "nextfixturesurl",
}
_PAGE_PAIRS = (
    ("currentpage", "totalpages"),
    ("page", "totalpages"),
    ("page", "pagecount"),
)
_PAGE_KEYS = frozenset(key for pair in _PAGE_PAIRS for key in pair)
_PAGE_COMPANIONS = {
    "currentpage": ("totalpages",),
    "page": ("totalpages", "pagecount"),
    "totalpages": ("currentpage", "page"),
    "pagecount": ("page",),
}
_PAGE_CANONICAL_NAMES = {
    "currentpage": "currentPage",
    "page": "page",
    "totalpages": "totalPages",
    "pagecount": "pageCount",
}
_KNOWN_KEYS = frozenset({"hasmore", *_CONTINUATION_KEYS, *_PAGE_KEYS})


def _is_empty_continuation_marker(value: Any) -> bool:
    if value is None or value is False:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, tuple, dict, set)):
        return len(value) == 0
    return False


def inspect_known_pagination(raw: Any) -> dict[str, Any]:
    """Inspect only direct continuation metadata under ``raw["fixtures"]``."""

    detected: list[str] = []
    unresolved: list[str] = []
    fixtures = raw.get("fixtures") if isinstance(raw, dict) else None
    if not isinstance(fixtures, dict):
        return {
            "status": "NOT_DETECTED",
            "evidence": [],
            "detected_evidence": [],
            "unresolved_evidence": [],
        }

    path = "$.fixtures"
    marker_index: dict[str, list[tuple[str, Any]]] = {}
    for key, value in fixtures.items():
        original_key = str(key)
        normalized_key = original_key.casefold()
        if normalized_key in _KNOWN_KEYS:
            marker_index.setdefault(normalized_key, []).append(
                (original_key, value),
            )

    collided_keys = {
        normalized_key
        for normalized_key, entries in marker_index.items()
        if len(entries) > 1
    }
    for normalized_key in collided_keys:
        for original_key, _ in marker_index[normalized_key]:
            unresolved.append(
                f"collision:{normalized_key}:{path}.{original_key}"
            )

    unique_markers = {
        normalized_key: entries[0]
        for normalized_key, entries in marker_index.items()
        if len(entries) == 1
    }

    if "hasmore" in unique_markers:
        original_key, marker_value = unique_markers["hasmore"]
        marker_path = f"{path}.{original_key}"
        if marker_value is True:
            detected.append(marker_path)
        elif marker_value is not False and marker_value is not None:
            unresolved.append(marker_path)

    for key in _CONTINUATION_KEYS:
        if key not in unique_markers:
            continue
        original_key, marker_value = unique_markers[key]
        marker_path = f"{path}.{original_key}"
        if _is_empty_continuation_marker(marker_value):
            continue
        if isinstance(marker_value, str):
            detected.append(marker_path)
        else:
            unresolved.append(marker_path)

    complete_page_pairs = [
        (current_key, total_key)
        for current_key, total_key in _PAGE_PAIRS
        if current_key in unique_markers and total_key in unique_markers
    ]
    paired_page_keys = {
        key for pair in complete_page_pairs for key in pair
    }
    page_pair_outcomes: list[tuple[str, str]] = []
    for current_key, total_key in complete_page_pairs:
        current_name, current_value = unique_markers[current_key]
        total_name, total_value = unique_markers[total_key]
        pair_path = f"{path}.{current_name}/{total_name}"
        valid_numbers = (
            isinstance(current_value, int)
            and not isinstance(current_value, bool)
            and isinstance(total_value, int)
            and not isinstance(total_value, bool)
            and current_value >= 0
            and total_value >= 0
        )
        if not valid_numbers:
            unresolved.append(pair_path)
            page_pair_outcomes.append((pair_path, "MALFORMED"))
        elif current_value < total_value:
            detected.append(pair_path)
            page_pair_outcomes.append((pair_path, "DETECTED"))
        elif current_value > total_value:
            unresolved.append(pair_path)
            page_pair_outcomes.append((pair_path, "MALFORMED"))
        else:
            page_pair_outcomes.append((pair_path, "NO_CONTINUATION"))

    semantic_outcomes = [
        (pair_path, outcome)
        for pair_path, outcome in page_pair_outcomes
        if outcome in {"DETECTED", "NO_CONTINUATION"}
    ]
    if len({outcome for _, outcome in semantic_outcomes}) > 1:
        conflict_parts = sorted(
            f"{pair_path}={outcome}"
            for pair_path, outcome in semantic_outcomes
        )
        unresolved.append(
            "conflict:page-dialects:" + "|".join(conflict_parts)
        )

    page_family_present = set(marker_index) & _PAGE_KEYS
    orphan_page_keys = (
        page_family_present - paired_page_keys - collided_keys
    )
    for normalized_key in sorted(orphan_page_keys):
        original_name, _ = unique_markers[normalized_key]
        unresolved.append(f"incomplete:present:{path}.{original_name}")
        for companion in _PAGE_COMPANIONS[normalized_key]:
            if companion not in marker_index:
                companion_name = _PAGE_CANONICAL_NAMES[companion]
                unresolved.append(
                    f"incomplete:missing-companion:{path}.{companion_name}"
                )

    detected_evidence = sorted(set(detected))
    unresolved_evidence = sorted(set(unresolved))
    evidence = sorted(set(detected_evidence + unresolved_evidence))
    if unresolved_evidence:
        status = "UNRESOLVED"
    elif detected_evidence:
        status = "DETECTED"
    else:
        status = "NOT_DETECTED"
    return {
        "status": status,
        "evidence": evidence,
        "detected_evidence": detected_evidence,
        "unresolved_evidence": unresolved_evidence,
    }
