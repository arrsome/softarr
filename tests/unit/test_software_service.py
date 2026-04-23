"""Unit tests for SoftwareService pagination (TBI-17)."""

import math

from softarr.schemas.software import PaginatedSoftwareResponse


class TestPaginatedSoftwareResponse:
    """Schema unit tests -- no DB required."""

    def test_build_single_page(self):
        resp = PaginatedSoftwareResponse.build(items=[], total=5, page=1, page_size=50)
        assert resp.total == 5
        assert resp.page == 1
        assert resp.page_size == 50
        assert resp.total_pages == 1

    def test_build_multiple_pages(self):
        resp = PaginatedSoftwareResponse.build(
            items=[], total=105, page=2, page_size=50
        )
        assert resp.total_pages == 3  # ceil(105/50)
        assert resp.page == 2

    def test_build_zero_page_size(self):
        resp = PaginatedSoftwareResponse.build(items=[], total=10, page=1, page_size=0)
        assert resp.total_pages == 0

    def test_build_exact_page_boundary(self):
        resp = PaginatedSoftwareResponse.build(
            items=[], total=100, page=1, page_size=50
        )
        assert resp.total_pages == 2

    def test_build_single_entry(self):
        resp = PaginatedSoftwareResponse.build(items=[], total=1, page=1, page_size=50)
        assert resp.total_pages == 1

    def test_total_pages_formula(self):
        """total_pages == ceil(total / page_size) for all sizes."""
        for total in [0, 1, 49, 50, 51, 99, 100, 101]:
            for page_size in [10, 25, 50]:
                expected = math.ceil(total / page_size) if page_size > 0 else 0
                resp = PaginatedSoftwareResponse.build(
                    items=[], total=total, page=1, page_size=page_size
                )
                assert resp.total_pages == expected, (
                    f"total={total} page_size={page_size}: "
                    f"expected {expected}, got {resp.total_pages}"
                )
