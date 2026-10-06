"""Control type and section lookup tables for Access objects.

Extracted from AccessAdapter (plan 060). These are pure lookup tables
with no AccessAdapter state dependency.

AcControlType values verified against the Access 16.0 type library on
2026-10-05 (plan 098): the previous table was shifted and created wrong
control types live (e.g. "CommandButton" -> 102 = acLine).
"""


# Verified Access AcControlType constants (typelib ground truth).
_TYPE_MAP: dict[int, str] = {
    100: "Label",
    101: "Rectangle",
    102: "Line",
    103: "Image",
    104: "CommandButton",
    105: "OptionButton",
    106: "CheckBox",
    107: "OptionGroup",
    108: "BoundObjectFrame",
    109: "TextBox",
    110: "ListBox",
    111: "ComboBox",
    112: "SubForm",
    114: "ObjectFrame",
    118: "PageBreak",
    119: "CustomControl",
    122: "ToggleButton",
    123: "TabControl",
    124: "Page",
    126: "Attachment",
    127: "EmptyCell",
    128: "WebBrowserControl",
    129: "NavigationControl",
    130: "NavigationButton",
}


def control_type_name(ctrl_type: int) -> str:
    """Map Access AcControlType integer to readable name."""
    return _TYPE_MAP.get(ctrl_type, f"Control({ctrl_type})")


def control_type_id(name: str) -> int:
    """Map readable control type name to Access AcControlType integer."""
    by_name = {v: k for k, v in _TYPE_MAP.items()}
    return by_name.get(name, 0)


def section_name(section_id: int) -> str:
    """Map Access AcSection integer to readable name."""
    section_map = {
        0: "detail",
        1: "header",
        2: "footer",
        3: "page_header",
        4: "page_footer",
    }
    return section_map.get(section_id, f"Section({section_id})")


def section_id(name: str) -> int:
    """Map readable section name to Access AcSection integer."""
    section_map = {
        "detail": 0,
        "header": 1,
        "footer": 2,
        "page_header": 3,
        "page_footer": 4,
    }
    return section_map.get(name.lower(), 0)
