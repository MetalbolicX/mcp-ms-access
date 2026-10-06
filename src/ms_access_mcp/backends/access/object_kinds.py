"""Generic-by-kind helpers for form/report objects.

Plan 060: extracted from AccessAdapter to reduce facade size.
Functions take dispatcher as first arg — stateless w.r.t. AccessAdapter beyond connection access.
"""

from collections.abc import Callable
from typing import Any, TypeVar

from ...errors import AccessBackendError, AccessObjectNotFoundError

_T = TypeVar("_T")

# Registry for form/report clone-family deduplication.
# Maps kind key to COM collection names and Access object-type constants.
OBJECT_KINDS = {
    "form": {"collection": "AllForms", "ac_type": 2, "open_verb": "OpenForm", "live_collection": "Forms", "close_ac": 2},
    "report": {"collection": "AllReports", "ac_type": 3, "open_verb": "OpenReport", "live_collection": "Reports", "close_ac": 3},
}


def _open_object_in_design(dispatcher, kind: str, object_name: str) -> None:
    """Open a form/report in design view via the kind's DoCmd verb."""
    open_verb = OBJECT_KINDS[kind]["open_verb"]
    getattr(dispatcher.access_app.DoCmd, open_verb)(object_name, 1)  # acDesign=1


def _live_object(dispatcher, kind: str, object_name: str) -> Any:
    """Resolve the live form/report in design view (None when unavailable).

    Form side prefers Screen.ActiveForm and falls back to Forms(name);
    report side reads Reports(name) directly. A raising Forms(name)
    fallback propagates to the caller's handler, matching legacy behavior.
    """
    app = dispatcher.access_app
    if kind == "form":
        try:
            return app.Screen.ActiveForm
        except Exception:
            return app.Forms(object_name)
    try:
        collection = OBJECT_KINDS[kind]["live_collection"]
        return getattr(app, collection)(object_name)
    except Exception:
        return None


def _with_design_view(
    dispatcher,
    kind: str,
    object_name: str,
    *,
    save_flag: int | Callable[[], int],
    work: Callable[[Any], _T],
    on_error: Callable[[BaseException], _T],
    default: _T,
    open_object_in_design: Callable[[Any, str, str], None] = _open_object_in_design,
    live_object: Callable[[Any, str, str], Any] = _live_object,
) -> _T:
    """Open kind/name in design view on the STA thread, run work(live_obj), always close once.

    Owns the guard / open / close lifecycle so the 14 call sites cannot drift.
    Close failures are swallowed (best-effort cleanup); work failures are routed
    through on_error (getters return a default, setters log-and-False).
    """
    import logging
    logger = logging.getLogger(__name__)

    if not dispatcher._started:
        return default

    def _do() -> _T:
        try:
            open_object_in_design(dispatcher, kind, object_name)
            obj = live_object(dispatcher, kind, object_name)
            return work(obj)
        except Exception as exc:
            return on_error(exc)
        finally:
            try:
                close_ac = OBJECT_KINDS[kind]["close_ac"]
                close_save_flag = save_flag() if callable(save_flag) else save_flag
                dispatcher.access_app.DoCmd.Close(close_ac, object_name, close_save_flag)
            except Exception as exc:
                logger.debug("design-view close %s/%s failed: %s", kind, object_name, exc)
                pass

    return dispatcher.call(_do)


def _object_exists(dispatcher, kind: str, name: str) -> bool:
    """Check if an object of the given kind (form/report) exists."""
    import logging
    logger = logging.getLogger(__name__)

    if not dispatcher._started:
        return False

    def _do() -> bool:
        try:
            collection_name = OBJECT_KINDS[kind]["collection"]
            all_objects = getattr(dispatcher.access_app.CurrentProject, collection_name)
            for i in range(all_objects.Count):
                if all_objects(i).Name == name:
                    return True
        except Exception as exc:
            logger.debug("object_exists %s/%s collection access failed: %s", kind, name, exc)
            pass
        return False

    try:
        return dispatcher.call(_do)
    except Exception as exc:
        logger.debug("object_exists %s/%s dispatcher failed: %s", kind, name, exc)
        return False


def _create_object(
    dispatcher,
    kind: str,
    object_name: str,
    record_source: str = "",
    template_name: str = "",
    properties: dict[str, Any] | None = None,
    log_object_failure: Callable[[str, str, str, BaseException], None] | None = None,
) -> bool:
    """Create and name a form or report through the Application COM API.

    ``template_name`` remains accepted for API compatibility but is not
    applied; Access cannot create from a template through the verified flow.
    """
    import logging

    logger = logging.getLogger(__name__)

    if not dispatcher._started:
        return False

    # Access cannot apply template_name through the verified creation API.
    ac_type: int | None = None
    default_name: str | None = None

    def _create() -> str:
        nonlocal default_name
        app = dispatcher.access_app
        if kind == "form":
            obj = app.CreateForm()
        else:
            # Verified live 2026-10-05: reports persist with the same recipe
            # as forms (CreateReport -> Close(acReport, name, acSaveYes) ->
            # Rename), provided ac_type is the real acReport=3. With the
            # historical wrong value 4 (acMacro) Close opens a hidden modal
            # and the COM call hangs.
            obj = app.CreateReport()
        created_name = obj.Name
        default_name = created_name
        if record_source:
            try:
                obj.RecordSource = record_source
            except Exception as exc:
                logger.debug(
                    "create_%s: RecordSource %r rejected: %s",
                    kind,
                    record_source,
                    exc,
                )
        if properties:
            for prop_name, value in properties.items():
                try:
                    obj.Properties(prop_name).Value = value
                except Exception as exc:
                    logger.warning(
                        "create_%s: property %s rejected: %s",
                        kind,
                        prop_name,
                        exc,
                    )
        return created_name

    try:
        ac_type = OBJECT_KINDS[kind]["ac_type"]
        default_name = dispatcher.call(_create)
        dispatcher.call(
            lambda: dispatcher.access_app.DoCmd.Close(ac_type, default_name, 1)
        )  # acSaveYes=1
        dispatcher.call(
            lambda: dispatcher.access_app.DoCmd.Rename(
                object_name, ac_type, default_name
            )
        )
        return True
    except Exception as exc:
        if default_name is not None and ac_type is not None:
            try:
                # A separate dispatch call is required: Close invalidates the
                # current DoCmd proxy, and acSaveNo prevents prompt leakage.
                dispatcher.call(
                    lambda: dispatcher.access_app.DoCmd.Close(
                        ac_type, default_name, 2
                    )
                )
            except Exception as cleanup_exc:
                logger.debug(
                    "create_%s: acSaveNo cleanup for %s failed: %s",
                    kind,
                    default_name,
                    cleanup_exc,
                )
        if log_object_failure:
            log_object_failure(kind, "create", object_name, exc)
        return False


def _classify_object_failure(kind: str, operation: str, object_name: str, exc: BaseException) -> AccessObjectNotFoundError | AccessBackendError:
    """Classify a COM failure: object_name/0x8004 in message → not-found, else backend."""
    if object_name in str(exc) or "0x8004" in str(exc):
        return AccessObjectNotFoundError(kind, object_name)
    return AccessBackendError(operation, object_name, str(exc))


def _log_object_failure(kind: str, operation: str, object_name: str, exc: BaseException) -> None:
    """Classify a COM failure and log it as a warning (for bool-collapsing operations)."""
    import logging
    logger = logging.getLogger(__name__)

    error = _classify_object_failure(kind, operation, object_name, exc)
    logger.warning(
        "%s %s '%s' failed: %s: %s",
        operation,
        kind,
        object_name,
        type(error).__name__,
        error,
    )


def _rename_object(dispatcher, kind: str, old_name: str, new_name: str) -> bool:
    """Rename a form or report via DoCmd.Rename."""
    import logging
    logger = logging.getLogger(__name__)

    if not dispatcher._started:
        return False

    def _do() -> bool:
        try:
            ac_type = OBJECT_KINDS[kind]["ac_type"]
            dispatcher.access_app.DoCmd.Rename(new_name, ac_type, old_name)
            return True
        except Exception as exc:
            raise _classify_object_failure(kind, "rename", old_name, exc) from exc

    try:
        return dispatcher.call(_do)
    except (AccessObjectNotFoundError, AccessBackendError) as exc:
        logger.warning(
            "rename %s '%s' failed: %s: %s",
            kind,
            old_name,
            type(exc).__name__,
            exc,
        )
        return False


def _delete_object(dispatcher, kind: str, object_name: str) -> bool:
    """Delete a form or report from the database."""
    import logging
    logger = logging.getLogger(__name__)

    if not dispatcher._started:
        return False

    def _do() -> bool:
        try:
            ac_type = OBJECT_KINDS[kind]["ac_type"]
            dispatcher.access_app.DoCmd.DeleteObject(ac_type, object_name)
            return True
        except Exception as exc:
            raise _classify_object_failure(kind, "delete", object_name, exc) from exc

    try:
        return dispatcher.call(_do)
    except (AccessObjectNotFoundError, AccessBackendError) as exc:
        logger.warning(
            "delete %s '%s' failed: %s: %s",
            kind,
            object_name,
            type(exc).__name__,
            exc,
        )
        return False
