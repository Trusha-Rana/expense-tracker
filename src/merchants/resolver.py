"""
resolver.py — Merchant Resolution Engine
=========================================
PROBLEM: Raw PDF merchant names are messy and inconsistent.
  "TIM HORTONS #1234 BRAMPTON ON"
  "TMHRTNS BRMPTON"
  "Tim Horton's Coffee"
  ... all refer to the same merchant.

RESOLUTION PIPELINE (in order, first match wins):
  1. DB exact lookup     — raw_name already known → return linked merchant
  2. YAML rule match     — pattern matches a config rule → create/link merchant
  3. DB fuzzy lookup     — fuzzy match against known canonical names in DB
  4. YAML fuzzy lookup   — fuzzy match against canonical names in YAML rules
  5. Unresolved          — flag for manual review in the UI

WHY THIS ORDER?
  - Exact DB lookup is O(1) — check it first
  - YAML rules handle abbreviated names that fuzzy matching fails on
  - DB fuzzy lookup leverages your growing knowledge base
  - YAML fuzzy is a static fallback for first-time merchants
"""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import yaml
from rapidfuzz import fuzz, process
from sqlalchemy.orm import Session

from src.storage.models import Category, Merchant, MerchantRawName

# ── Config ──────────────────────────────────────────────────────────────────────

_CONFIG_PATH = Path(__file__).parent.parent.parent / "config" / "merchant_rules.yaml"
_FUZZY_THRESHOLD = 80  # Minimum RapidFuzz score (0-100) to accept a match


@dataclass
class ResolutionResult:
    canonical_name: str
    merchant_id: Optional[int]     # None if not yet in DB
    method: str                     # "exact_db" | "rule" | "fuzzy_db" | "fuzzy_yaml" | "unresolved"
    score: float                    # 0.0 – 1.0 confidence
    needs_review: bool


class MerchantResolver:
    """
    Stateful resolver that loads YAML rules once and caches DB merchant names.
    Instantiate once per Streamlit session and reuse.
    """

    def __init__(self, session: Session):
        self.session = session
        self._yaml_rules: list[dict] = self._load_yaml_rules()
        self._db_canonical_names: list[str] = []   # cache refreshed on demand

    # ── Setup ─────────────────────────────────────────────────────────────────

    def _load_yaml_rules(self) -> list[dict]:
        if not _CONFIG_PATH.exists():
            return []
        with open(_CONFIG_PATH, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return data.get("rules", [])

    def refresh_db_cache(self) -> None:
        """Reloads canonical merchant names from DB. Call after inserts."""
        self._db_canonical_names = [
            m.canonical_name
            for m in self.session.query(Merchant.canonical_name).all()
        ]

    # ── Normalization ─────────────────────────────────────────────────────────

    @staticmethod
    def _normalize(name: str) -> str:
        """
        Prepares a raw name for matching:
          - Lowercase
          - Remove store numbers (#1234, store 05)
          - Remove Canadian province codes at end
          - Remove extra whitespace
        This makes "TIM HORTONS #1234 BRAMPTON ON" → "tim hortons brampton"
        """
        name = name.lower().strip()
        name = re.sub(r"#\d+", "", name)               # Remove #1234
        name = re.sub(r"\bstore\s*\d+\b", "", name)    # Remove store 5
        name = re.sub(r"\b(on|bc|ab|qc|sk|mb|ns|nb|pe|nl|yt|nt|nu)\b", "", name)  # Province
        name = re.sub(r"\s{2,}", " ", name).strip()
        return name

    # ── Step 1: Exact DB lookup ───────────────────────────────────────────────

    def _lookup_exact_db(self, raw_name: str) -> Optional[ResolutionResult]:
        """
        Checks if this exact raw name is already in the merchant_raw_names table.
        This is the hot path — previously seen names resolve instantly.
        """
        row = (
            self.session.query(MerchantRawName)
            .filter(MerchantRawName.raw_name == raw_name)
            .first()
        )
        if row:
            return ResolutionResult(
                canonical_name=row.merchant.canonical_name,
                merchant_id=row.merchant_id,
                method="exact_db",
                score=1.0,
                needs_review=False,
            )
        return None

    # ── Step 2: YAML rule matching ────────────────────────────────────────────

    def _lookup_yaml_rule(self, raw_name: str) -> Optional[ResolutionResult]:
        """
        Checks the YAML rules file. Supports both plain substring and regex patterns.
        """
        normalized = self._normalize(raw_name)

        for rule in self._yaml_rules:
            canonical = rule["canonical"]
            for pattern in rule.get("patterns", []):
                if pattern.startswith("regex:"):
                    regex = pattern[len("regex:"):]
                    if re.search(regex, normalized, re.IGNORECASE):
                        return ResolutionResult(
                            canonical_name=canonical,
                            merchant_id=None,   # Will be set after DB upsert
                            method="rule",
                            score=0.95,
                            needs_review=False,
                        )
                else:
                    if pattern.lower() in normalized:
                        return ResolutionResult(
                            canonical_name=canonical,
                            merchant_id=None,
                            method="rule",
                            score=0.95,
                            needs_review=False,
                        )
        return None

    # ── Step 3 & 4: Fuzzy matching ────────────────────────────────────────────

    def _lookup_fuzzy(
        self, raw_name: str, candidates: list[str], method: str
    ) -> Optional[ResolutionResult]:
        """
        Uses RapidFuzz WRatio scorer (handles acronyms, partial matches, transpositions).
        WRatio is better than simple ratio for merchant names because it tries
        multiple matching strategies and returns the best score.
        """
        if not candidates:
            return None

        normalized = self._normalize(raw_name)
        match = process.extractOne(
            normalized,
            candidates,
            scorer=fuzz.WRatio,
            score_cutoff=_FUZZY_THRESHOLD,
        )
        if match:
            matched_canonical, score, _ = match
            return ResolutionResult(
                canonical_name=matched_canonical,
                merchant_id=None,
                method=method,
                score=round(score / 100, 3),
                needs_review=score < 90,   # Flag borderline matches for review
            )
        return None

    # ── DB upsert helpers ─────────────────────────────────────────────────────

    def _get_or_create_merchant(self, canonical_name: str) -> Merchant:
        """
        Finds or creates a Merchant row by canonical name.
        Uses get-or-create pattern to avoid race conditions / duplicate inserts.
        """
        merchant = (
            self.session.query(Merchant)
            .filter(Merchant.canonical_name == canonical_name)
            .first()
        )
        if not merchant:
            merchant = Merchant(canonical_name=canonical_name)
            self.session.add(merchant)
            self.session.flush()   # Get the auto-generated ID without committing
        return merchant

    def _link_raw_name(
        self, raw_name: str, merchant: Merchant, score: float, source: str
    ) -> None:
        """
        Adds raw_name → merchant mapping to merchant_raw_names table.
        This is what makes future exact lookups fast.
        """
        exists = (
            self.session.query(MerchantRawName)
            .filter(MerchantRawName.raw_name == raw_name)
            .first()
        )
        if not exists:
            link = MerchantRawName(
                merchant_id=merchant.id,
                raw_name=raw_name,
                match_score=score,
                source=source,
            )
            self.session.add(link)

    # ── Main resolution method ────────────────────────────────────────────────

    def resolve(self, raw_name: str) -> ResolutionResult:
        """
        Runs the full resolution pipeline for a single raw merchant name.
        Saves any new mappings back to the DB for future reuse.
        """
        # Step 1: Exact DB match
        result = self._lookup_exact_db(raw_name)
        if result:
            return result

        # Step 2: YAML rules
        result = self._lookup_yaml_rule(raw_name)
        if result:
            merchant = self._get_or_create_merchant(result.canonical_name)
            self._link_raw_name(raw_name, merchant, result.score, "rule")
            result.merchant_id = merchant.id
            return result

        # Step 3: Fuzzy match against DB canonical names
        self.refresh_db_cache()
        result = self._lookup_fuzzy(raw_name, self._db_canonical_names, "fuzzy_db")
        if result:
            merchant = self._get_or_create_merchant(result.canonical_name)
            self._link_raw_name(raw_name, merchant, result.score, "fuzzy")
            result.merchant_id = merchant.id
            return result

        # Step 4: Fuzzy match against YAML canonical names
        yaml_canonicals = [r["canonical"] for r in self._yaml_rules]
        result = self._lookup_fuzzy(raw_name, yaml_canonicals, "fuzzy_yaml")
        if result:
            merchant = self._get_or_create_merchant(result.canonical_name)
            self._link_raw_name(raw_name, merchant, result.score, "fuzzy")
            result.merchant_id = merchant.id
            return result

        # Step 5: Unresolved — create an unresolved merchant entry
        merchant = self._get_or_create_merchant(raw_name)  # Use raw as canonical placeholder
        self._link_raw_name(raw_name, merchant, 0.0, "manual")
        return ResolutionResult(
            canonical_name=raw_name,
            merchant_id=merchant.id,
            method="unresolved",
            score=0.0,
            needs_review=True,
        )

    def resolve_batch(self, raw_names: list[str]) -> list[ResolutionResult]:
        """
        Resolves a list of merchant names. Commits once at the end for efficiency.
        The session commit is the caller's responsibility (inside get_session()).
        """
        return [self.resolve(name) for name in raw_names]