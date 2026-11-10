"""Use standard text rules without publishing an identifier as a display name."""
from backend.vendor.miaodi_standard.contract import name_or_none


def display_name(value, source_reference, fallback):
    name = name_or_none(value, source_reference)
    return name if name is not None else fallback
