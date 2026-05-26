"""
tests/test_core.py — Unit Tests
================================
Tests the three core engines independently (no DB required).
Run with: pytest tests/ -v
"""

import pytest
from datetime import date
from unittest.mock import MagicMock, patch


# ──────────────────────────────────────────────────────────────────────────────
# PDF PARSER TESTS
# ──────────────────────────────────────────────────────────────────────────────

class TestDateParsing:
    def test_standard_date(self):
        from src.parsing.pdf_parser import _parse_date
        result = _parse_date("Jan 15, 2024", 2024)
        assert result == date(2024, 1, 15)

    def test_date_without_year_uses_default(self):
        from src.parsing.pdf_parser import _parse_date
        result = _parse_date("Jan 15", 2023)
        assert result == date(2023, 1, 15)

    def test_slash_date_format(self):
        from src.parsing.pdf_parser import _parse_date
        result = _parse_date("01/15/2024", 2024)
        assert result == date(2024, 1, 15)

    def test_invalid_date_returns_none(self):
        from src.parsing.pdf_parser import _parse_date
        result = _parse_date("NOT A DATE", 2024)
        assert result is None


class TestAmountParsing:
    def test_simple_debit(self):
        from src.parsing.pdf_parser import _parse_amount
        amount, txn_type = _parse_amount("4.57")
        assert amount == 4.57
        assert txn_type == "debit"

    def test_credit_suffix(self):
        from src.parsing.pdf_parser import _parse_amount
        amount, txn_type = _parse_amount("150.00CR")
        assert amount == 150.00
        assert txn_type == "credit"

    def test_parentheses_as_credit(self):
        from src.parsing.pdf_parser import _parse_amount
        amount, txn_type = _parse_amount("(25.00)")
        assert amount == 25.00
        assert txn_type == "credit"

    def test_dollar_sign_stripped(self):
        from src.parsing.pdf_parser import _parse_amount
        amount, txn_type = _parse_amount("$1,234.56")
        assert amount == 1234.56
        assert txn_type == "debit"

    def test_negative_sign_as_credit(self):
        from src.parsing.pdf_parser import _parse_amount
        amount, txn_type = _parse_amount("-99.00")
        assert amount == 99.00
        assert txn_type == "credit"


class TestBankDetection:
    def test_detects_td(self):
        from src.parsing.pdf_parser import _detect_bank
        assert _detect_bank("TD Bank Financial Group Statement") == "TD"

    def test_detects_rbc(self):
        from src.parsing.pdf_parser import _detect_bank
        assert _detect_bank("Royal Bank of Canada Credit Card Statement") == "RBC"

    def test_unknown_bank(self):
        from src.parsing.pdf_parser import _detect_bank
        assert _detect_bank("Some Random Financial Institution") == "Unknown"


# ──────────────────────────────────────────────────────────────────────────────
# MERCHANT RESOLVER TESTS
# ──────────────────────────────────────────────────────────────────────────────

class TestMerchantNormalization:
    def test_remove_store_number(self):
        from src.merchants.resolver import MerchantResolver
        result = MerchantResolver._normalize("TIM HORTONS #1234")
        assert "#1234" not in result
        assert "tim hortons" in result

    def test_remove_province(self):
        from src.merchants.resolver import MerchantResolver
        result = MerchantResolver._normalize("TIM HORTONS BRAMPTON ON")
        assert " on" not in result

    def test_lowercase(self):
        from src.merchants.resolver import MerchantResolver
        result = MerchantResolver._normalize("STARBUCKS")
        assert result == "starbucks"

    def test_collapse_spaces(self):
        from src.merchants.resolver import MerchantResolver
        result = MerchantResolver._normalize("TIM   HORTONS")
        assert "  " not in result


class TestYamlRuleMatching:
    """Test YAML rule matching without a real DB."""

    def _make_resolver(self):
        mock_session = MagicMock()
        mock_session.query.return_value.filter.return_value.first.return_value = None
        resolver = object.__new__(
            __import__("src.merchants.resolver", fromlist=["MerchantResolver"]).MerchantResolver
        )
        resolver.session = mock_session
        resolver._yaml_rules = resolver._load_yaml_rules()
        resolver._db_canonical_names = []
        return resolver

    def test_tim_hortons_abbreviation(self):
        resolver = self._make_resolver()
        result = resolver._lookup_yaml_rule("TMHRTNS BRAMPTON")
        assert result is not None
        assert result.canonical_name == "Tim Hortons"
        assert result.method == "rule"

    def test_mcdonalds_variant(self):
        resolver = self._make_resolver()
        result = resolver._lookup_yaml_rule("MCDONALDS #12345 TORONTO")
        assert result is not None
        assert result.canonical_name == "McDonald's"

    def test_amazon_variant(self):
        resolver = self._make_resolver()
        result = resolver._lookup_yaml_rule("AMZN MKTPL*AB1CD2EF3")
        assert result is not None
        assert result.canonical_name == "Amazon"

    def test_unknown_returns_none(self):
        resolver = self._make_resolver()
        result = resolver._lookup_yaml_rule("XYZZY COMPLETELY UNKNOWN MERCHANT")
        assert result is None


# ──────────────────────────────────────────────────────────────────────────────
# CATEGORIZER TESTS
# ──────────────────────────────────────────────────────────────────────────────

class TestRuleBasedCategorizer:
    def _make_categorizer(self):
        from src.categorize.categorizer import RuleBasedCategorizer, CategoryRules
        rules = CategoryRules()
        return RuleBasedCategorizer(rules)

    def test_categorizes_grocery(self):
        cat, _ = self._make_categorizer().categorize("Loblaws Store 42", "")
        assert cat == "Groceries"

    def test_categorizes_streaming(self):
        cat, _ = self._make_categorizer().categorize("Netflix", "")
        assert cat == "Entertainment & Streaming"

    def test_categorizes_dining(self):
        cat, _ = self._make_categorizer().categorize("Tim Hortons", "")
        assert cat == "Dining & Restaurants"

    def test_categorizes_transport(self):
        cat, _ = self._make_categorizer().categorize("Petro-Canada", "")
        assert cat == "Transportation"

    def test_unknown_returns_none(self):
        cat, score = self._make_categorizer().categorize("XYZZY UNKNOWN", "")
        assert cat is None
        assert score == 0.0

    def test_confidence_higher_for_merchant_than_description(self):
        categorizer = self._make_categorizer()
        _, merch_confidence = categorizer.categorize("Netflix", "")
        _, desc_confidence  = categorizer.categorize("Unknown Merchant", "netflix subscription")
        assert merch_confidence > desc_confidence


# ──────────────────────────────────────────────────────────────────────────────
# FORECASTER TESTS
# ──────────────────────────────────────────────────────────────────────────────

class TestLinearForecast:
    def test_increasing_trend(self):
        import pandas as pd
        from src.forecasts.forecaster import SpendForecaster
        mock_session = MagicMock()
        forecaster = SpendForecaster(mock_session)
        series = pd.Series([100.0, 150.0, 200.0, 250.0])
        prediction = forecaster._linear_forecast(series)
        # Should predict ~300
        assert 250 <= prediction <= 350

    def test_no_negative_predictions(self):
        import pandas as pd
        from src.forecasts.forecaster import SpendForecaster
        mock_session = MagicMock()
        forecaster = SpendForecaster(mock_session)
        # Declining to near zero
        series = pd.Series([100.0, 50.0, 10.0, 5.0])
        prediction = forecaster._linear_forecast(series)
        assert prediction >= 0.0
