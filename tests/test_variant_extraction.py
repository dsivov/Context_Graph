"""Tests for product variant document formatting — CR-012.

Covers:
- format_product_doc() variant table rendering
- Available Colors / Available Sizes summary lines
- Edge cases: 0 variants, 20-variant cap
- _parse_variant_attributes() size/color extraction
- _collect_variant_values() field collection
"""
from __future__ import annotations

import sys
import os

import pytest

# sync_all.py is in scripts/, not a package
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from sync_all import (
    format_product_doc,
    _parse_variant_attributes,
    _collect_variant_values,
)


# ─────────────────────────────────────────────────────────────────────────────
# format_product_doc — variant table
# ─────────────────────────────────────────────────────────────────────────────


class TestFormatProductDocVariants:
    def test_produces_variant_table_from_info_colors(self):
        product = {
            "title": "Test Bedding",
            "information": {"colors": ["White", "Blue", "Gray"]},
        }
        doc = format_product_doc(product, "TestCo")
        assert "## Product Variants" in doc
        assert "| Variant Name | Color | Price |" in doc
        assert "Test Bedding — Blue" in doc
        assert "Test Bedding — Gray" in doc
        assert "Test Bedding — White" in doc

    def test_produces_variant_table_from_info_sizes(self):
        product = {
            "title": "Test Mattress",
            "price": "999 NIS",
            "information": {"size_options": ["120x190", "140x200"]},
        }
        doc = format_product_doc(product, "TestCo")
        assert "## Product Variants" in doc
        assert "| Variant Name | Size | Price |" in doc
        assert "Test Mattress — 120x190" in doc
        assert "Test Mattress — 140x200" in doc

    def test_produces_cross_product_table_from_colors_and_sizes(self):
        product = {
            "title": "Test Product",
            "price": "500",
            "information": {
                "colors": ["Red", "Blue"],
                "sizes": ["S", "M"],
            },
        }
        doc = format_product_doc(product, "TestCo")
        assert "## Product Variants" in doc
        assert "| Variant Name | Size | Color | Price |" in doc
        # Cross-product: 2 sizes × 2 colors = 4 rows
        assert "Test Product — M Blue" in doc
        assert "Test Product — M Red" in doc
        assert "Test Product — S Blue" in doc
        assert "Test Product — S Red" in doc

    def test_produces_variant_table_from_explicit_variants_array(self):
        product = {
            "title": "Legacy Product",
            "variants": [
                {"title": "Small Red", "price": "100", "sku": "LP-SR"},
                {"title": "Large Blue", "price": "200", "sku": "LP-LB"},
            ],
        }
        doc = format_product_doc(product, "TestCo")
        assert "## Product Variants" in doc
        assert "Legacy Product — Small Red" in doc
        assert "LP-SR" in doc

    def test_available_colors_summary(self):
        product = {
            "title": "Color Test",
            "information": {"colorsAvailable": ["Green Sage", "Old Pink", "Cream"]},
        }
        doc = format_product_doc(product, "TestCo")
        assert "Available Colors: Cream, Green Sage, Old Pink" in doc

    def test_available_sizes_summary(self):
        product = {
            "title": "Size Test",
            "information": {"availableSizes": ["90x200", "120x200", "160x200"]},
        }
        doc = format_product_doc(product, "TestCo")
        assert "Available Sizes:" in doc
        assert "90x200" in doc
        assert "160x200" in doc

    def test_no_variant_table_for_zero_variants(self):
        product = {"title": "Simple Product", "price": "99 NIS"}
        doc = format_product_doc(product, "TestCo")
        assert "## Product Variants" not in doc
        assert "Available Colors" not in doc
        assert "Available Sizes" not in doc

    def test_variant_cap_at_20(self):
        product = {
            "title": "Many Variants",
            "variants": [
                {"title": f"Variant {i}", "price": f"{i}", "sku": f"V-{i}"}
                for i in range(30)
            ],
        }
        doc = format_product_doc(product, "TestCo")
        row_count = doc.count("| Many Variants —")
        assert row_count == 20

    def test_cross_product_cap_at_20(self):
        product = {
            "title": "Big Cross",
            "information": {
                "colors": [f"Color{i}" for i in range(10)],
                "sizes": [f"Size{i}" for i in range(10)],
            },
        }
        doc = format_product_doc(product, "TestCo")
        row_count = doc.count("| Big Cross —")
        assert row_count == 20


# ─────────────────────────────────────────────────────────────────────────────
# _parse_variant_attributes
# ─────────────────────────────────────────────────────────────────────────────


class TestParseVariantAttributes:
    def test_size_pattern(self):
        size, color = _parse_variant_attributes("120x190")
        assert "120×190" in size
        assert color == ""

    def test_named_size(self):
        size, color = _parse_variant_attributes("King 160×200")
        assert "King" in size
        assert "160×200" in size

    def test_color_only(self):
        size, color = _parse_variant_attributes("Stone Gray")
        assert size == ""
        assert color == "Stone Gray"

    def test_size_and_color(self):
        size, color = _parse_variant_attributes("160×200 Charcoal Gray")
        assert "160×200" in size
        assert color == "Charcoal Gray"


# ─────────────────────────────────────────────────────────────────────────────
# _collect_variant_values
# ─────────────────────────────────────────────────────────────────────────────


class TestCollectVariantValues:
    def test_collects_from_list_field(self):
        info = {"colors": ["Red", "Blue", "Green"]}
        result = _collect_variant_values(info, ["colors"])
        assert result == {"Red", "Blue", "Green"}

    def test_collects_from_multiple_fields(self):
        info = {"colors": ["Red"], "colorsAvailable": ["Blue"]}
        result = _collect_variant_values(info, ["colors", "colorsAvailable"])
        assert result == {"Red", "Blue"}

    def test_handles_comma_separated_string(self):
        info = {"sizes": "S, M, L"}
        result = _collect_variant_values(info, ["sizes"])
        assert result == {"S", "M", "L"}

    def test_empty_when_no_fields(self):
        info = {"unrelated": "data"}
        result = _collect_variant_values(info, ["colors", "sizes"])
        assert result == set()

    def test_skips_empty_values(self):
        info = {"colors": ["Red", "", "  ", "Blue"]}
        result = _collect_variant_values(info, ["colors"])
        assert result == {"Red", "Blue"}
