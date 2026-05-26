"""
pdf_parser.py — Credit Card Statement PDF Parser
=================================================
CHALLENGE: Credit card PDFs have no standard format.
  - TD has columns: Date | Description | Amount
  - RBC uses a different column order and includes running balance
  - Some banks output scanned images (not text) — requires OCR

STRATEGY:
  1. Try pdfplumber first (best for text-based PDFs — uses PDF content stream)
  2. If pdfplumber extracts no usable data, fall back to PyMuPDF
  3. Apply a series of regex patterns to find transaction rows
  4. Return a normalized DataFrame regardless of the source bank

OUTPUT SCHEMA (always the same, regardless of bank):
  - transaction_date : date
  - raw_merchant     : str    (exactly as it appears in the PDF)
  - amount           : float  (always positive; type is separate)
  - transaction_type : str    ("debit" | "credit")
  - description      : str    (full raw line for debugging)
"""

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import pandas as pd
import pdfplumber

try:
    import fitz  # PyMuPDF — optional fallback
    PYMUPDF_AVAILABLE = True
except ImportError:
    PYMUPDF_AVAILABLE = False


# ── Data classes ────────────────────────────────────────────────────────────────

@dataclass
class ParsedTransaction:
    transaction_date: date
    raw_merchant: str
    amount: float
    transaction_type: str   # "debit" | "credit"
    description: str = ""


@dataclass
class ParseResult:
    """Returned by parse_statement() — wraps the DataFrame + metadata."""
    transactions: pd.DataFrame
    raw_text: str
    page_count: int
    detected_bank: str
    parse_method: str           # "pdfplumber" | "pymupdf"
    errors: list[str] = field(default_factory=list)

    @property
    def success(self) -> bool:
        return len(self.transactions) > 0


# ── Date parsing helpers ────────────────────────────────────────────────────────

_DATE_FORMATS = [
    "%b %d, %Y",   # Jan 15, 2024
    "%b %d %Y",    # Jan 15 2024
    "%b. %d",      # Jan. 15  (no year — we'll inject current year)
    "%b %d",       # Jan 15
    "%B %d, %Y",   # January 15, 2024
    "%B %d",       # January 15
    "%m/%d/%Y",    # 01/15/2024
    "%m/%d/%y",    # 01/15/24
    "%d/%m/%Y",    # 15/01/2024
    "%Y-%m-%d",    # 2024-01-15
    "%m-%d-%Y",    # 01-15-2024
]

def _parse_date(raw: str, default_year: int) -> Optional[date]:
    """
    Tries each date format until one works.
    Many statements omit the year (e.g. "Jan 15") — we inject default_year.
    """
    raw = raw.strip()
    for fmt in _DATE_FORMATS:
        try:
            dt = datetime.strptime(raw, fmt)
            if dt.year == 1900:            # strptime default when no year in fmt
                dt = dt.replace(year=default_year)
            return dt.date()
        except ValueError:
            continue
    return None


# ── Amount parsing ──────────────────────────────────────────────────────────────

def _parse_amount(raw: str) -> tuple[float, str]:
    """
    Returns (amount_as_positive_float, transaction_type).

    Credit card statements show credits (refunds, payments) with CR suffix
    or in parentheses or with a negative sign. Everything else is a debit.
    """
    raw = raw.strip().replace(",", "")   # Remove thousand separators

    is_credit = False
    if raw.endswith("CR") or raw.endswith("cr"):
        is_credit = True
        raw = raw[:-2].strip()
    elif raw.startswith("(") and raw.endswith(")"):
        is_credit = True
        raw = raw[1:-1]
    elif raw.startswith("-"):
        is_credit = True
        raw = raw[1:]

    try:
        amount = abs(float(raw.replace("$", "").strip()))
        return amount, "credit" if is_credit else "debit"
    except ValueError:
        return 0.0, "debit"


# ── Bank detection ──────────────────────────────────────────────────────────────

def _detect_bank(text: str) -> str:
    """
    Simple heuristic: look for bank names in the first ~500 chars.
    This drives which regex pattern set to use.
    """
    sample = text[:500].lower()
    if "td bank" in sample or "toronto-dominion" in sample:
        return "TD"
    if "royal bank" in sample or "rbc" in sample:
        return "RBC"
    if "bank of montreal" in sample or "bmo" in sample:
        return "BMO"
    if "scotiabank" in sample or "bank of nova scotia" in sample:
        return "Scotia"
    if "cibc" in sample:
        return "CIBC"
    if "tangerine" in sample:
        return "Tangerine"
    if "american express" in sample or "amex" in sample:
        return "Amex"
    return "Unknown"


# ── Core regex patterns for transaction lines ───────────────────────────────────
#
# ANATOMY OF A TRANSACTION LINE (varies by bank):
#
#   TD:     "Jan 15  TIM HORTONS #1234 BRAMPTON ON  4.57"
#   RBC:    "01/15   TIM HORTONS 1234                4.57"
#   CIBC:   "Jan 15, 2024  Tim Hortons              $4.57"
#
# We use a flexible pattern that captures:
#   Group 1: date string (various formats)
#   Group 2: merchant / description (everything in between)
#   Group 3: amount (with optional $ and CR suffix)

_TRANSACTION_PATTERNS = [
    # Pattern A: "Mon DD  MERCHANT NAME  AMOUNT" (TD-style, 2+ spaces between cols)
    re.compile(
        r"^([A-Z][a-z]{2}\.?\s+\d{1,2}(?:,?\s+\d{4})?)"   # date
        r"\s{2,}"
        r"(.+?)"                                              # merchant (lazy)
        r"\s{2,}"
        r"(\(?\$?[\d,]+\.\d{2}\s*(?:CR|cr)?\)?)"           # amount
        r"\s*$",
        re.MULTILINE,
    ),
    # Pattern B: "MM/DD  MERCHANT  AMOUNT" (RBC/CIBC-style, 2+ spaces)
    re.compile(
        r"^(\d{2}/\d{2}(?:/\d{2,4})?)"
        r"\s{2,}"
        r"(.+?)"
        r"\s{2,}"
        r"(\(?\$?[\d,]+\.\d{2}\s*(?:CR|cr)?\)?)"
        r"\s*$",
        re.MULTILINE,
    ),
    # Pattern C: "YYYY-MM-DD  MERCHANT  AMOUNT" (ISO date, 2+ spaces)
    re.compile(
        r"^(\d{4}-\d{2}-\d{2})"
        r"\s{2,}"
        r"(.+?)"
        r"\s{2,}"
        r"(\(?\$?[\d,]+\.\d{2}\s*(?:CR|cr)?\)?)"
        r"\s*$",
        re.MULTILINE,
    ),
    # Pattern D: "Mon DD MERCHANT $AMOUNT[ CR]" — single-space pdfplumber table output
    # Amount is anchored by leading $ sign, making lazy merchant match unambiguous.
    # Also handles "Mon DD MERCHANT $AMOUNT CR" (space before CR).
    re.compile(
        r"^([A-Z][a-z]{2}\.?\s+\d{1,2}(?:,?\s+\d{4})?)"   # date: "Apr 02"
        r"\s+"
        r"(.+?)"                                              # merchant (lazy)
        r"\s+"
        r"(\$[\d,]+\.\d{2}(?:\s+CR)?)"                      # amount: "$6.75" or "$49.99 CR"
        r"\s*$",
        re.MULTILINE,
    ),
    # Pattern E: "MM/DD MERCHANT $AMOUNT[ CR]" — single-space with slash date
    re.compile(
        r"^(\d{2}/\d{2}(?:/\d{2,4})?)"
        r"\s+"
        r"(.+?)"
        r"\s+"
        r"(\$[\d,]+\.\d{2}(?:\s+CR)?)"
        r"\s*$",
        re.MULTILINE,
    ),
]

# Lines to skip — headers, totals, page markers, column headers
_SKIP_PATTERNS = re.compile(
    r"(opening balance|closing balance|previous balance|total new charges"
    r"|minimum payment|payment due|page \d|statement date|credit limit"
    r"|available credit|interest charged|transaction date"
    r"|^date\s+description|account summary|account holder|account number"
    r"|statement period|credit card statement)",
    re.IGNORECASE,
)


def _extract_transactions_from_text(
    text: str, default_year: int
) -> list[ParsedTransaction]:
    """
    Runs all regex patterns against the raw text.
    Deduplicates matches by (date, merchant, amount) to handle multi-page PDFs
    where the same section is repeated.
    """
    found: list[ParsedTransaction] = []
    seen: set[tuple] = set()

    for pattern in _TRANSACTION_PATTERNS:
        for match in pattern.finditer(text):
            raw_date, raw_merchant, raw_amount = match.groups()

            # Skip header/summary lines
            if _SKIP_PATTERNS.search(raw_merchant):
                continue

            parsed_date = _parse_date(raw_date.strip(), default_year)
            if parsed_date is None:
                continue

            amount, txn_type = _parse_amount(raw_amount)
            if amount <= 0:
                continue

            merchant = raw_merchant.strip()
            key = (parsed_date, merchant.lower(), amount)
            if key in seen:
                continue
            seen.add(key)

            found.append(ParsedTransaction(
                transaction_date=parsed_date,
                raw_merchant=merchant,
                amount=amount,
                transaction_type=txn_type,
                description=match.group(0).strip(),
            ))

    return found


# ── pdfplumber extraction ───────────────────────────────────────────────────────

def _extract_with_pdfplumber(pdf_path: Path) -> tuple[str, int]:
    """
    Extracts all text from a PDF using pdfplumber.

    pdfplumber is better than PyMuPDF for tabular PDFs because it understands
    column layout — words are sorted left-to-right, top-to-bottom per page.
    We join pages with a separator so regex multi-line mode works correctly.
    """
    pages_text = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        page_count = len(pdf.pages)
        for page in pdf.pages:
            # extract_text() returns None if page has no text layer (scanned)
            text = page.extract_text(x_tolerance=3, y_tolerance=3)
            if text:
                pages_text.append(text)

    return "\n\n--- PAGE BREAK ---\n\n".join(pages_text), page_count


# ── PyMuPDF fallback ────────────────────────────────────────────────────────────

def _extract_with_pymupdf(pdf_path: Path) -> tuple[str, int]:
    """
    Fallback extraction using PyMuPDF (fitz).
    Better for PDFs with complex layouts or mixed text/image content.
    """
    if not PYMUPDF_AVAILABLE:
        return "", 0

    doc = fitz.open(str(pdf_path))
    pages_text = []
    for page in doc:
        text = page.get_text("text")
        if text.strip():
            pages_text.append(text)
    doc.close()
    return "\n\n--- PAGE BREAK ---\n\n".join(pages_text), len(doc)


# ── Public API ──────────────────────────────────────────────────────────────────

def parse_statement(
    pdf_path: str | Path,
    statement_year: Optional[int] = None,
) -> ParseResult:
    """
    Main entry point. Parses a PDF credit card statement and returns
    a normalized DataFrame of transactions.

    Args:
        pdf_path:       Path to the PDF file
        statement_year: Year to use when dates in PDF omit the year.
                        Defaults to current year.

    Returns:
        ParseResult with .transactions DataFrame and metadata.

    The returned DataFrame always has these columns:
        transaction_date | raw_merchant | amount | transaction_type | description
    """
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    year = statement_year or datetime.now().year
    errors: list[str] = []

    # ── Step 1: Extract raw text ────────────────────────────────────────────
    raw_text, page_count = _extract_with_pdfplumber(pdf_path)
    parse_method = "pdfplumber"

    if not raw_text.strip() and PYMUPDF_AVAILABLE:
        # PDF might be image-based — try PyMuPDF
        raw_text, page_count = _extract_with_pymupdf(pdf_path)
        parse_method = "pymupdf"
        if not raw_text.strip():
            errors.append("PDF appears to be scanned (image-only). OCR required.")

    # ── Step 2: Detect bank ─────────────────────────────────────────────────
    detected_bank = _detect_bank(raw_text)

    # ── Step 3: Extract transactions ────────────────────────────────────────
    parsed_rows = _extract_transactions_from_text(raw_text, default_year=year)

    if not parsed_rows:
        errors.append(
            "No transactions found. The statement format may not be supported yet. "
            "Check the raw text output in the Upload page for debugging."
        )

    # ── Step 4: Build DataFrame ─────────────────────────────────────────────
    df = pd.DataFrame([
        {
            "transaction_date": r.transaction_date,
            "raw_merchant":     r.raw_merchant,
            "amount":           r.amount,
            "transaction_type": r.transaction_type,
            "description":      r.description,
        }
        for r in parsed_rows
    ])

    if not df.empty:
        df["transaction_date"] = pd.to_datetime(df["transaction_date"])
        df = df.sort_values("transaction_date").reset_index(drop=True)

    return ParseResult(
        transactions=df,
        raw_text=raw_text,
        page_count=page_count,
        detected_bank=detected_bank,
        parse_method=parse_method,
        errors=errors,
    )