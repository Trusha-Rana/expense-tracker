"""
categorizer.py — Transaction Categorization Engine
====================================================
DESIGN: Two-phase categorization with AI fallback.

PHASE 1 — Rule-Based (active now):
  Loads categories.yaml keyword rules. For each transaction:
    1. Check if merchant already has a category in DB → use it instantly
    2. Scan keyword rules against normalized merchant name + description
    3. Return best match with a confidence score

PHASE 2 — AI-Powered (activated by CATEGORIZATION_MODE=ai or hybrid in .env):
  If rule-based confidence < AI_FALLBACK_THRESHOLD (default 0.6):
    → Call Claude API with merchant name + description
    → Parse structured JSON response
    → Store result + set source = "ai"

WHY KEYWORD CONFIDENCE SCORES?
  Multiple keyword rules can partially match a transaction.
  We assign partial scores so the UI can flag borderline cases
  and the AI fallback only kicks in when truly needed — saving API cost.
"""

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import yaml
from anthropic import Anthropic
from sqlalchemy.orm import Session

from src.storage.models import Category, Merchant, Transaction, CategorizationSource


# ── Config ──────────────────────────────────────────────────────────────────────

_CONFIG_PATH = Path(__file__).parent.parent.parent / "config" / "categories.yaml"
_MODE = os.getenv("CATEGORIZATION_MODE", "rule_based")
_AI_THRESHOLD = float(os.getenv("AI_FALLBACK_THRESHOLD", "0.6"))
_AI_MAX_TOKENS = int(os.getenv("AI_MAX_TOKENS", "200"))


@dataclass
class CategorizationResult:
    category_name: str
    category_id: Optional[int]
    source: CategorizationSource
    confidence: float           # 0.0 – 1.0
    needs_review: bool


# ── Rule loader ─────────────────────────────────────────────────────────────────

class CategoryRules:
    """Parses and holds categories.yaml. Loaded once per app session."""

    def __init__(self):
        self.categories: list[dict] = []
        self._load()

    def _load(self):
        if not _CONFIG_PATH.exists():
            return
        with open(_CONFIG_PATH, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        self.categories = data.get("categories", [])

    def get_names(self) -> list[str]:
        return [c["name"] for c in self.categories]

    def seed_db(self, session: Session) -> None:
        """
        Ensures all YAML categories exist in the DB.
        Called once at startup so the DB is always in sync with the config.
        Idempotent — safe to call multiple times.
        """
        for cat in self.categories:
            existing = (
                session.query(Category)
                .filter(Category.name == cat["name"])
                .first()
            )
            if not existing:
                session.add(Category(
                    name=cat["name"],
                    spending_type=cat["spending_type"],
                    color_hex=cat.get("color_hex"),
                    icon=cat.get("icon"),
                ))
        session.flush()


# ── Rule-based engine ────────────────────────────────────────────────────────────

class RuleBasedCategorizer:
    """
    Keyword-matching categorizer.

    Scoring:
      - Full keyword match in merchant name → 0.9 confidence
      - Keyword match in description only  → 0.7 confidence
      - No match → 0.0 (triggers AI fallback or "Other")
    """

    def __init__(self, rules: CategoryRules):
        self.rules = rules

    def _normalize(self, text: str) -> str:
        return text.lower().strip() if text else ""

    def categorize(
        self,
        merchant_name: str,
        description: str = "",
    ) -> tuple[Optional[str], float]:
        """
        Returns (category_name, confidence_score).
        Returns (None, 0.0) if nothing matched.
        """
        norm_merchant = self._normalize(merchant_name)
        norm_desc     = self._normalize(description)

        for cat in self.rules.categories:
            keywords = cat.get("keywords", [])
            if not keywords:  # Skip "Other" — it's the fallback
                continue

            for kw in keywords:
                kw = kw.lower()
                if kw in norm_merchant:
                    return cat["name"], 0.9
                if kw in norm_desc:
                    return cat["name"], 0.7

        return None, 0.0


# ── AI categorization engine ─────────────────────────────────────────────────────

class AICategorizer:
    """
    Uses Claude API to categorize transactions that rule-based couldn't handle.

    Prompt design:
      - Provide the category list so Claude picks from valid options only
      - Ask for JSON response → easy to parse, no hallucinated categories
      - Include reasoning field → useful for debugging and UI display
    """

    def __init__(self, category_names: list[str]):
        self.client = Anthropic()
        self.category_names = category_names
        self._category_list_str = "\n".join(f"  - {c}" for c in category_names)

    def categorize(self, merchant_name: str, description: str = "") -> tuple[Optional[str], float]:
        """
        Calls Claude and returns (category_name, confidence).
        Returns (None, 0.0) on any error.
        """
        prompt = f"""You are categorizing a credit card transaction.

Merchant: {merchant_name}
Description: {description or "N/A"}

Choose the BEST category from this list only:
{self._category_list_str}

Respond ONLY with valid JSON, no explanation, no markdown:
{{"category": "<exact category name>", "confidence": <0.0-1.0>, "reasoning": "<one sentence>"}}"""

        try:
            response = self.client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=_AI_MAX_TOKENS,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = response.content[0].text.strip()

            # Strip any accidental markdown code fences
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            raw = raw.strip()

            data = json.loads(raw)
            category = data.get("category", "").strip()
            confidence = float(data.get("confidence", 0.5))

            # Validate that the returned category is in our list
            if category not in self.category_names:
                return None, 0.0

            return category, confidence

        except Exception:
            return None, 0.0


# ── Main categorization orchestrator ─────────────────────────────────────────────

class TransactionCategorizer:
    """
    Orchestrates rule-based + AI categorization.

    Mode (from .env CATEGORIZATION_MODE):
      "rule_based" → only use keyword rules
      "ai"         → use AI for everything
      "hybrid"     → rules first, AI fallback for low-confidence results
    """

    def __init__(self, session: Session):
        self.session = session
        self.rules = CategoryRules()
        self.rules.seed_db(session)

        self.rule_engine = RuleBasedCategorizer(self.rules)

        self.ai_engine: Optional[AICategorizer] = None
        if _MODE in ("ai", "hybrid") and os.getenv("ANTHROPIC_API_KEY"):
            self.ai_engine = AICategorizer(self.rules.get_names())

        # Cache: category_name → Category ORM object
        self._category_cache: dict[str, Category] = {}

    def _get_category(self, name: str) -> Optional[Category]:
        if name in self._category_cache:
            return self._category_cache[name]
        cat = self.session.query(Category).filter(Category.name == name).first()
        if cat:
            self._category_cache[name] = cat
        return cat

    def _get_merchant_category(self, merchant_id: Optional[int]) -> Optional[Category]:
        """
        If the merchant already has a category assigned (and verified), use it.
        This is the fastest path and gives consistent results.
        """
        if not merchant_id:
            return None
        merchant = self.session.query(Merchant).get(merchant_id)
        if merchant and merchant.category_id and merchant.is_verified:
            return merchant.category
        return None

    def categorize_transaction(
        self,
        merchant_name: str,
        description: str = "",
        merchant_id: Optional[int] = None,
    ) -> CategorizationResult:
        """
        Categorizes a single transaction. Resolution order:
          1. Merchant's existing category (if verified in DB)
          2. Rule-based keyword matching
          3. AI categorization (if enabled and confidence too low)
          4. Fallback to "Other / Uncategorized"
        """
        # Path 1: Use the merchant's already-assigned category
        existing_cat = self._get_merchant_category(merchant_id)
        if existing_cat:
            return CategorizationResult(
                category_name=existing_cat.name,
                category_id=existing_cat.id,
                source=CategorizationSource.RULE,
                confidence=1.0,
                needs_review=False,
            )

        # Path 2: Rule-based
        rule_category, rule_confidence = self.rule_engine.categorize(
            merchant_name, description
        )

        if _MODE == "rule_based" or not self.ai_engine:
            # No AI — use rule result or fall back to "Other"
            cat_name = rule_category or "Other / Uncategorized"
            cat_obj  = self._get_category(cat_name)
            return CategorizationResult(
                category_name=cat_name,
                category_id=cat_obj.id if cat_obj else None,
                source=CategorizationSource.RULE if rule_category else CategorizationSource.UNKNOWN,
                confidence=rule_confidence if rule_category else 0.0,
                needs_review=not bool(rule_category),
            )

        # Path 3: AI fallback (hybrid or ai mode)
        if _MODE == "ai" or rule_confidence < _AI_THRESHOLD:
            ai_category, ai_confidence = self.ai_engine.categorize(
                merchant_name, description
            )
            if ai_category:
                cat_obj = self._get_category(ai_category)
                return CategorizationResult(
                    category_name=ai_category,
                    category_id=cat_obj.id if cat_obj else None,
                    source=CategorizationSource.AI,
                    confidence=ai_confidence,
                    needs_review=ai_confidence < 0.75,
                )

        # Use the rule result if it was above threshold (hybrid, rule passed)
        if rule_category:
            cat_obj = self._get_category(rule_category)
            return CategorizationResult(
                category_name=rule_category,
                category_id=cat_obj.id if cat_obj else None,
                source=CategorizationSource.RULE,
                confidence=rule_confidence,
                needs_review=False,
            )

        # Path 4: Fallback
        fallback = self._get_category("Other / Uncategorized")
        return CategorizationResult(
            category_name="Other / Uncategorized",
            category_id=fallback.id if fallback else None,
            source=CategorizationSource.UNKNOWN,
            confidence=0.0,
            needs_review=True,
        )

    def categorize_batch(
        self,
        transactions: list[dict],
    ) -> list[CategorizationResult]:
        """
        Categorizes a list of dicts with keys: merchant_name, description, merchant_id.
        """
        return [
            self.categorize_transaction(
                merchant_name=t.get("merchant_name", ""),
                description=t.get("description", ""),
                merchant_id=t.get("merchant_id"),
            )
            for t in transactions
        ]