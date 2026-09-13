"""Query analysis — SQL parsing, complexity analysis, and recommendations.

This module is ported from the oracle's services/query_analyzer.py, adapted to use
this repo's Ok|Err result envelope and adapter seam.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

__all__ = ["QueryAnalyzerService", "SQLParseResult", "generate_recommendations"]


# =============================================================================
# SQL Parse Result
# =============================================================================


@dataclass
class SQLParseResult:
    """Parsed SQL statement with structural metadata."""

    sql: str
    statement_type: str  # SELECT, INSERT, UPDATE, DELETE, OTHER
    tables_involved: list[str] = field(default_factory=list)
    where_clause_present: bool = False
    join_count: int = 0
    subquery_count: int = 0
    order_by_present: bool = False
    group_by_present: bool = False
    having_present: bool = False
    distinct_present: bool = False
    limit_present: bool = False
    union_present: bool = False
    function_count: int = 0
    parameter_count: int = 0


# =============================================================================
# SQL Complexity Analyzer
# =============================================================================


class SQLComplexityAnalyzer:
    """Parse SQL and extract structural metadata for complexity analysis."""

    STATEMENT_RE = re.compile(
        r"^\s*(SELECT|INSERT|UPDATE|DELETE|ALTER|CREATE|DROP|TRUNCATE)\s",
        re.IGNORECASE,
    )
    TABLE_RE = re.compile(
        r"\bFROM\s+([\w`\[\] ]+?)(?:\s+|$)",
        re.IGNORECASE | re.DOTALL,
    )
    WHERE_RE = re.compile(r"\bWHERE\b", re.IGNORECASE)
    JOIN_RE = re.compile(
        r"\bJOIN\s+([\w`\[\]]+?)(?:\s+|$)",
        re.IGNORECASE,
    )
    SUBQUERY_RE = re.compile(r"\bSELECT\b.*\bFROM\b.*\(", re.IGNORECASE | re.DOTALL)
    ORDER_RE = re.compile(r"\bORDER\s+BY\b", re.IGNORECASE)
    GROUP_RE = re.compile(r"\bGROUP\s+BY\b", re.IGNORECASE)
    HAVING_RE = re.compile(r"\bHAVING\b", re.IGNORECASE)
    DISTINCT_RE = re.compile(r"\bSELECT\s+DISTINCT\b", re.IGNORECASE)
    LIMIT_RE = re.compile(r"\bLIMIT\b", re.IGNORECASE)
    UNION_RE = re.compile(r"\bUNION\b", re.IGNORECASE)
    FUNCTION_RE = re.compile(
        r"\b(COUNT|SUM|AVG|MIN|MAX|COALESCE|IIF|NZ|LEFT|RIGHT|MID|TRIM|"
        r"LCASE|UCASE|ROUND|FORMAT|DATEADD|DATEDIFF|DATE\(|NOW\(|YEAR\(|"
        r"MONTH\(|DAY\(|DATEFORMAT)\s*\(",
        re.IGNORECASE,
    )
    PARAM_RE = re.compile(r"\?")
    TABLE_NAME_RE = re.compile(
        r"\bFROM\s+([\w `[\]]+?)(?:\s+|$)",
        re.IGNORECASE,
    )

    @classmethod
    def parse(cls, sql: str) -> SQLParseResult | None:
        """Parse SQL and extract structural metadata.

        Args:
            sql: SQL statement to parse

        Returns:
            SQLParseResult with structural metadata, or None if not parseable
        """
        if not sql or not sql.strip():
            return None

        match = cls.STATEMENT_RE.match(sql.strip())
        if not match:
            return SQLParseResult(sql=sql, statement_type="OTHER")

        statement_type = match.group(1).upper()

        where_present = bool(cls.WHERE_RE.search(sql))
        join_count = len(cls.JOIN_RE.findall(sql))
        subquery_count = len(cls.SUBQUERY_RE.findall(sql))
        order_present = bool(cls.ORDER_RE.search(sql))
        group_present = bool(cls.GROUP_RE.search(sql))
        having_present = bool(cls.HAVING_RE.search(sql))
        distinct_present = bool(cls.DISTINCT_RE.search(sql))
        limit_present = bool(cls.LIMIT_RE.search(sql))
        union_present = bool(cls.UNION_RE.search(sql))
        function_count = len(cls.FUNCTION_RE.findall(sql))
        param_count = len(cls.PARAM_RE.findall(sql))

        # Extract table names
        table_names: list[str] = []
        for tbl_match in cls.TABLE_NAME_RE.finditer(sql):
            for group in tbl_match.groups():
                if group and group.upper() not in ("SELECT", "WHERE", "JOIN"):
                    name = group.strip("`[]\"").upper()
                    if name not in table_names:
                        table_names.append(name)

        return SQLParseResult(
            sql=sql,
            statement_type=statement_type,
            tables_involved=table_names,
            where_clause_present=where_present,
            join_count=join_count,
            subquery_count=subquery_count,
            order_by_present=order_present,
            group_by_present=group_present,
            having_present=having_present,
            distinct_present=distinct_present,
            limit_present=limit_present,
            union_present=union_present,
            function_count=function_count,
            parameter_count=param_count,
        )


# =============================================================================
# Schema Analyzer
# =============================================================================


class SchemaAnalyzer:
    """Analyze schema information for tables involved in a query."""

    @classmethod
    def analyze(
        cls,
        get_table_schema_fn: Any,
        table_names: list[str],
    ) -> dict[str, Any]:
        """Get schema info for tables involved in a query.

        Args:
            get_table_schema_fn: Callable that takes a table name and returns schema
            table_names: List of table names to analyze

        Returns:
            Dict mapping table name to schema info
        """
        schema: dict[str, Any] = {}
        for table_name in table_names:
            try:
                schema[table_name] = get_table_schema_fn(table_name)
            except Exception:
                schema[table_name] = {"error": "Could not retrieve schema"}
        return schema


# =============================================================================
# Recommendations Generator
# =============================================================================


def generate_recommendations(
    parse_result: SQLParseResult,
    schema: dict[str, Any],
    execution: dict[str, Any] | None,
    sql: str = "",
) -> list[str]:
    """Generate performance recommendations based on parse result and execution.

    Args:
        parse_result: Parsed SQL structure
        schema: Schema information for tables
        execution: Optional execution timing info
        sql: Original SQL (for context)

    Returns:
        List of recommendation strings
    """
    recommendations: list[str] = []

    if parse_result.subquery_count > 0:
        recommendations.append(
            f"Subqueries detected ({parse_result.subquery_count}). "
            "Consider rewriting as JOINs for better performance."
        )

    if parse_result.join_count > 3:
        recommendations.append(
            f"Multiple JOINs ({parse_result.join_count}) detected. "
            "Verify indexes exist on join columns."
        )

    if not parse_result.where_clause_present and parse_result.statement_type == "SELECT":
        recommendations.append(
            "No WHERE clause found. Query will perform a full table scan."
        )

    if parse_result.distinct_present:
        recommendations.append(
            "DISTINCT used. Verify if duplicate removal is necessary."
        )

    if not parse_result.order_by_present and parse_result.statement_type == "SELECT":
        recommendations.append(
            "No ORDER BY clause. Results may be returned in unpredictable order."
        )

    # Check for functions in WHERE that prevent index usage
    if parse_result.function_count > 0:
        recommendations.append(
            "Functions in query may prevent index usage. "
            "Consider pre-computing or using computed columns."
        )

    if execution and execution.get("rowcount", 0) > 10000:
        recommendations.append(
            f"Large result set ({execution.get('rowcount')} rows). "
            "Consider adding LIMIT or pagination."
        )

    if not recommendations:
        recommendations.append("No obvious performance issues detected.")

    return recommendations


# =============================================================================
# Query Analyzer Service
# =============================================================================


class QueryAnalyzerService:
    """Analyze SQL queries for complexity, schema, and performance."""

    @classmethod
    def analyze(
        cls,
        sql: str,
        params: list[Any] | None,
        db_use_cases: Any,
        dry_run: bool = False,
        sample_size: int = 0,
    ) -> dict[str, Any]:
        """Analyze a SQL query.

        Args:
            sql: SQL statement to analyze
            params: Optional query parameters
            db_use_cases: DatabaseUseCases instance (adapter seam)
            dry_run: If True, skip execution and return structural analysis only
            sample_size: Number of rows to sample for timing (0 = full execution)

        Returns:
            Dict with analysis results: success, statement_type, tables_involved,
            complexity, recommendations, schema (if applicable), timing (if executed)
        """
        if not sql or not sql.strip():
            return {"success": False, "error": "Empty SQL statement"}

        # Parse SQL
        parse_result = SQLComplexityAnalyzer.parse(sql)
        if parse_result is None:
            return {"success": False, "error": "Could not parse SQL statement"}

        # Only SELECT can be fully analyzed via execution
        if parse_result.statement_type != "SELECT":
            return {
                "success": True,
                "statement_type": parse_result.statement_type,
                "tables_involved": parse_result.tables_involved,
                "complexity": cls._complexity_rating(parse_result),
                "is_select": False,
                "error": "Not a SELECT statement",
            }

        # Get schema for tables involved
        schema_info: dict[str, Any] = {}
        for table_name in parse_result.tables_involved:
            try:
                schema_result = db_use_cases.get_table_schema(table_name)
                if hasattr(schema_result, "is_ok") and schema_result.is_ok():
                    schema_info[table_name] = schema_result.unwrap()
                elif hasattr(schema_result, "data"):
                    schema_info[table_name] = schema_result["data"]
            except Exception:
                schema_info[table_name] = {}

        # Generate structural recommendations
        recommendations = generate_recommendations(parse_result, schema_info, None, sql)

        result: dict[str, Any] = {
            "success": True,
            "statement_type": parse_result.statement_type,
            "tables_involved": parse_result.tables_involved,
            "complexity": cls._complexity_rating(parse_result),
            "structure": {
                "where_clause_present": parse_result.where_clause_present,
                "join_count": parse_result.join_count,
                "subquery_count": parse_result.subquery_count,
                "order_by_present": parse_result.order_by_present,
                "group_by_present": parse_result.group_by_present,
                "distinct_present": parse_result.distinct_present,
                "function_count": parse_result.function_count,
                "parameter_count": parse_result.parameter_count,
            },
            "schema": schema_info,
            "recommendations": recommendations,
            "is_select": True,
        }

        # Execute if not dry run
        if not dry_run:
            try:
                query_result = db_use_cases.query_data(sql, params=params)
                if hasattr(query_result, "is_ok"):
                    if query_result.is_ok():
                        data = query_result.unwrap() if hasattr(query_result, "unwrap") else {}
                        rowcount = getattr(query_result, "rowcount", len(data)) if isinstance(data, (list, tuple)) else 0
                        result["timing"] = {"executed": True, "rowcount": rowcount}
                        result["recommendations"] = generate_recommendations(
                            parse_result, schema_info,
                            {"rowcount": rowcount}, sql
                        )
                    else:
                        result["timing"] = {
                            "executed": False,
                            "error": str(query_result) if query_result else "Unknown error"
                        }
                elif isinstance(query_result, dict):
                    if query_result.get("success"):
                        result["timing"] = {
                            "executed": True,
                            "rowcount": query_result.get("rowcount", 0)
                        }
                        result["recommendations"] = generate_recommendations(
                            parse_result, schema_info,
                            {"rowcount": query_result.get("rowcount", 0)}, sql
                        )
                    else:
                        result["timing"] = {
                            "executed": False,
                            "error": query_result.get("error", "Query failed")
                        }
            except Exception as e:
                result["timing"] = {"executed": False, "error": str(e)}

        return result

    @staticmethod
    def _complexity_rating(parse_result: SQLParseResult) -> str:
        """Rate query complexity based on structural features."""
        score = 0
        score += parse_result.subquery_count * 2
        score += parse_result.join_count
        score += parse_result.function_count
        if parse_result.where_clause_present:
            score += 1
        if parse_result.distinct_present:
            score += 1
        if parse_result.having_present:
            score += 2
        if parse_result.union_present:
            score += 2

        if score >= 8:
            return "HIGH"
        elif score >= 4:
            return "MEDIUM"
        else:
            return "LOW"
