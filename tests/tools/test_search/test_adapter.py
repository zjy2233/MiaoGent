"""Tests for SearchAdapter base class and result models."""

from __future__ import annotations

from src.tools.search.adapter import SearchResult, SearchResponse, _format_results


class TestSearchResult:
    def test_default_fields(self) -> None:
        r = SearchResult()
        assert r.title == ""
        assert r.url == ""
        assert r.snippet == ""
        assert r.source == ""

    def test_all_fields(self) -> None:
        r = SearchResult(title="T", url="U", snippet="S", source="test")
        assert r.title == "T"
        assert r.url == "U"
        assert r.snippet == "S"
        assert r.source == "test"


class TestSearchResponse:
    def test_default_fields(self) -> None:
        r = SearchResponse()
        assert r.results == []
        assert r.total == 0

    def test_with_results(self) -> None:
        results = [SearchResult(title="A"), SearchResult(title="B")]
        r = SearchResponse(query="test", results=results, total=2, source="mock")
        assert len(r.results) == 2
        assert r.source == "mock"


class TestFormatResults:
    def test_empty_results(self) -> None:
        text = _format_results("xyz", [], "mock")
        assert "未找到" in text
        assert "xyz" in text

    def test_single_result(self) -> None:
        results = [SearchResult(title="Python", url="https://python.org", snippet="A language")]
        text = _format_results("python", results, "mock")
        assert "Python" in text
        assert "python.org" in text
        assert "A language" in text

    def test_multiple_results(self) -> None:
        results = [
            SearchResult(title="A", url="http://a.com"),
            SearchResult(title="B", url="http://b.com"),
        ]
        text = _format_results("test", results, "mock")
        assert "前 2 条" in text
        assert "A" in text
        assert "B" in text
