"""Analysis use cases — ER diagrams and SQL generation."""

from __future__ import annotations

import dataclasses
from typing import Any

from ..errors import Err, Result, err, ok

_ok = ok
_err = err


def get_er_diagram(
    adapter: Any,
    get_tables_fn: Any,
    get_relationships_fn: Any,
) -> Result:
    """Get the database schema as nodes and edges for ER diagram visualization.

    Args:
        adapter: Resolved backend adapter
        get_tables_fn: Callable to get tables (connection_name) -> Result
        get_relationships_fn: Callable to get relationships (connection_name) -> Result

    Returns:
        Ok with dict containing nodes, edges, node_count, edge_count, or Err
    """
    tables_result = get_tables_fn()
    if isinstance(tables_result, Err):
        return tables_result

    rels_result = get_relationships_fn()
    if isinstance(rels_result, Err):
        return rels_result

    tables_data = tables_result.data
    rels_data = rels_result.data

    # Build nodes from table data
    nodes = []
    for table_dict in tables_data:
        nodes.append({
            "id": table_dict.get("name", ""),
            "type": "table",
            "data": {
                "label": table_dict.get("name", ""),
                "columns": table_dict.get("fields", []),
            },
        })

    # Build edges from relationship data
    edges = []
    for rel_dict in rels_data:
        edges.append({
            "id": rel_dict.get("name", ""),
            "source": rel_dict.get("foreign_table", ""),
            "target": rel_dict.get("table", ""),
            "label": rel_dict.get("name", ""),
            "animated": False,
        })

    return _ok(data={
        "nodes": nodes,
        "edges": edges,
        "node_count": len(nodes),
        "edge_count": len(edges),
    })


def generate_sql(
    adapter: Any,
    policy: Any,
    allowed_dirs: list[Any],
    output_path: str,
    _table_names: list[str] | None = None,
) -> Result:
    """Generate SQL DDL script for the database schema.

    Args:
        adapter: Resolved backend adapter
        policy: WritePolicy instance
        allowed_dirs: List of allowed directory paths
        output_path: Path to write the SQL script
        _table_names: Optional list of table names (currently unused)

    Returns:
        Ok on success, or Err
    """
    from ..safety import is_path_allowed

    if not is_path_allowed(output_path, allowed_dirs, must_exist=False):
        return _err(f"Output path not allowed: {output_path}")

    try:
        result = adapter.generate_sql(output_path)
        result_dict = dataclasses.asdict(result)
        if not result_dict.get("success", True):
            return _err(result_dict.get("error", "generate_sql failed"))
        return _ok(rowcount=1)
    except Exception as e:
        return _err(str(e))
