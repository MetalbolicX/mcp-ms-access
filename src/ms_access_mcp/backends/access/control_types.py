"""Control type and section lookup tables for Access objects.

Extracted from AccessAdapter (plan 060). These are pure lookup tables
with no AccessAdapter state dependency.
"""


def control_type_name(ctrl_type: int) -> str:
    """Map Access AcControlType integer to readable name."""
    type_map = {
        100: "TextBox",
        101: "Label",
        102: "CommandButton",
        103: "OptionButton",
        104: "ComboBox",
        105: "ListBox",
        106: "SubForm",
        107: "ToggleButton",
        108: "CheckBox",
        109: "OptionGroup",
        110: "TabControl",
        111: "Page",
        112: "Image",
        114: "BoundObjectFrame",
        115: "ObjectFrame",
        118: "Line",
        119: "Rectangle",
        120: "PageBreak",
        122: "Attachment",
        123: "NavigationButton",
        124: "NavigationControl",
        126: "WebBrowserControl",
        128: "EmptyCell",
    }
    return type_map.get(ctrl_type, f"Control({ctrl_type})")


def control_type_id(name: str) -> int:
    """Map readable control type name to Access AcControlType integer."""
    type_map = {
        "TextBox": 100,
        "Label": 101,
        "CommandButton": 102,
        "OptionButton": 103,
        "ComboBox": 104,
        "ListBox": 105,
        "SubForm": 106,
        "ToggleButton": 107,
        "CheckBox": 108,
        "OptionGroup": 109,
        "TabControl": 110,
        "Page": 111,
        "Image": 112,
        "BoundObjectFrame": 114,
        "ObjectFrame": 115,
        "Line": 118,
        "Rectangle": 119,
        "PageBreak": 120,
        "Attachment": 122,
        "NavigationButton": 123,
        "NavigationControl": 124,
        "WebBrowserControl": 126,
        "EmptyCell": 128,
    }
    return type_map.get(name, 0)


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
