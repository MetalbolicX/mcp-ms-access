"""Unit tests for Result envelope combinators (map/bind/fold)."""

from __future__ import annotations

from ms_access_mcp.errors import Err, Ok


class TestMap:
    def test_map_transforms_data_and_preserves_rowcount(self) -> None:
        result = Ok(data=[1, 2, 3], rowcount=3).map(lambda rows: [r * 10 for r in rows])
        assert result == Ok(data=[10, 20, 30], rowcount=3)

    def test_map_on_err_returns_same_err(self) -> None:
        failure = Err(message="boom")
        assert failure.map(lambda v: v + 1) == failure

    def test_map_composition_law(self) -> None:
        f = lambda v: v + 1  # noqa: E731
        g = lambda v: v * 2  # noqa: E731
        source = Ok(data=5, rowcount=1)
        assert source.map(f).map(g) == source.map(lambda v: g(f(v)))


class TestBind:
    def test_bind_chains_ok_to_ok(self) -> None:
        result = Ok(data=2, rowcount=1).bind(lambda v: Ok(data=v + 3, rowcount=1))
        assert result == Ok(data=5, rowcount=1)

    def test_bind_short_circuits_err_to_err(self) -> None:
        failure = Err(message="nope")
        assert failure.bind(lambda v: Ok(data=v)) == failure

    def test_bind_can_return_err_from_ok(self) -> None:
        result = Ok(data=None).bind(lambda _v: Err(message="empty"))
        assert result == Err(message="empty")


class TestFold:
    def test_fold_picks_on_ok_with_data_and_rowcount(self) -> None:
        seen: list[object] = []
        outcome = Ok(data="rows", rowcount=7).fold(
            lambda data, rowcount: seen.append((data, rowcount)) or "ok-branch",
            lambda message: seen.append(message) or "err-branch",
        )
        assert outcome == "ok-branch"
        assert seen == [("rows", 7)]

    def test_fold_picks_on_err_with_message(self) -> None:
        seen: list[object] = []
        outcome = Err(message="bad path").fold(
            lambda data, rowcount: seen.append((data, rowcount)) or "ok-branch",
            lambda message: seen.append(message) or "err-branch",
        )
        assert outcome == "err-branch"
        assert seen == ["bad path"]
