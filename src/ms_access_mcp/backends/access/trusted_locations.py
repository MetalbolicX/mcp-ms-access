"""Trusted locations wrapper for Access VBA project operations.

Extracted from AccessAdapter (plan 060). Wraps operations that modify VBA
code to preserve and restore Trusted Locations registry entries.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any, TypeVar

from ..trusted_locations import capture_trusted_locations, restore_trusted_locations

logger = logging.getLogger(__name__)

_TRUSTED_LOCATIONS_PRESERVE = False

_T = TypeVar("_T")


def trusted_locations_wrap(
    func: Callable[..., _T], *args: Any, **kwargs: Any
) -> _T:
    """Execute func(*args, **kwargs) with Trusted Locations preservation if enabled.

    Captures Trusted Locations before the call and restores after,
    controlled by ACCESS_MCP_PRESERVE_TRUSTED_LOCATIONS env var (default False).
    """
    global _TRUSTED_LOCATIONS_PRESERVE
    if not _TRUSTED_LOCATIONS_PRESERVE:
        return func(*args, **kwargs)

    captured = capture_trusted_locations()
    try:
        return func(*args, **kwargs)
    finally:
        if captured:
            restore_trusted_locations(captured)


def list_objects(
    dispatcher: Any,
    object_kinds: dict[str, dict[str, Any]],
    kind: str,
) -> list:
    """List all objects of a given kind (form/report) using the dispatcher.

    Args:
        dispatcher: ComDispatcher with ._started and .access_app attributes
        object_kinds: OBJECT_KINDS dict from AccessAdapter
        kind: "form" or "report"

    """
    from ms_access_mcp.models import FormInfo, ReportInfo

    if not dispatcher._started:
        return []

    def _do():
        objects = []
        skipped_props = 0
        skipped_objs = 0
        collection_name = object_kinds[kind]["collection"]
        try:
            all_objects = getattr(dispatcher.access_app.CurrentProject, collection_name)
            for i in range(all_objects.Count):
                obj = all_objects(i)
                try:
                    record_source = ""
                    try:
                        record_source = str(obj.Properties("RecordSource")) if obj.Properties.Exists("RecordSource") else ""
                    except Exception:
                        skipped_props += 1
                    model = FormInfo if kind == "form" else ReportInfo
                    objects.append(model(name=obj.Name, record_source=record_source))
                except Exception:
                    skipped_objs += 1
        except Exception:
            logger.debug("list_objects %s collection access failed", kind)
            pass
        if skipped_props or skipped_objs:
            logger.debug("list_objects %s: %d objects, %d property failures skipped", kind, skipped_objs, skipped_props)
        return objects

    try:
        return dispatcher.call(_do)
    except Exception as exc:
        logger.debug("list_objects %s dispatcher failed: %s", kind, exc)
        return []
