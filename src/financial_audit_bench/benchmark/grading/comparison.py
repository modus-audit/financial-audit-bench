"""Value comparison and identity normalization, independent of workbook layout."""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

# Comparison semantics shared by identity lookup and answer evaluation


COMPARISONS = frozenset(
    {
        "auto",
        "text",
        "numeric",
        "zero_or_blank",
        "date",
        "date_in_text",
        "date_range",
        "fiscal_period",
        "reference_set",
        "name",
        "check_reference",
        "payment_reference",
        "yn",
        "identifier",
        "required_words",
        "gl_account_set",
        "row_label",
        "procedure_number",
        "asset_class",
    }
)


def normalize_text(value: Any) -> str:
    return " ".join(str(value or "").split()).casefold()


def parse_number(value: Any) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float, Decimal)):
        number = Decimal(str(value))
        return number if number.is_finite() else None
    if not isinstance(value, str):
        return None
    candidate = value.strip().replace(",", "").replace("$", "")
    negative = candidate.startswith("(") and candidate.endswith(")")
    if negative:
        candidate = candidate[1:-1]
    if not re.fullmatch(r"[-+]?\d+(?:\.\d+)?", candidate):
        return None
    try:
        number = Decimal(candidate)
    except InvalidOperation:
        return None
    return -number if negative else number


def parse_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    for pattern in (
        "%m/%d/%Y",
        "%m/%d/%y",
        "%Y-%m-%d",
        "%B %d, %Y",
        "%b %d, %Y",
    ):
        try:
            return datetime.strptime(candidate, pattern).date()
        except ValueError:
            pass
    return None


def qualified_date(value: Any) -> date | None:
    plain_date = parse_date(value)
    if plain_date is not None or not isinstance(value, str):
        return plain_date
    # An explicit week-ending label still names exactly one complete date.
    week_ending = re.fullmatch(r"week\s+ending\s*(.+)", value.strip(), re.IGNORECASE)
    if week_ending is not None:
        return parse_date(week_ending.group(1))
    # Only one trailing, text-only qualifier; never extract dates from prose.
    match = re.fullmatch(r"([^()]+?)\s*\(([A-Za-z][A-Za-z \t-]*)\)", value.strip())
    if match is None or re.search(
        r"\b(?:not|no|non|never|wrong|incorrect|invalid|false|inaccurate|rejected|"
        r"excluded|disputed|or|nor|either|neither|other|alternative|alternate|"
        r"instead|rather|except|versus|vs)\b",
        match.group(2),
        re.IGNORECASE,
    ):
        return None
    return parse_date(match.group(1))


def _check_reference(value: Any) -> str | None:
    if isinstance(value, (int, float, Decimal)):
        number = parse_number(value)
        if number is not None and number >= 0 and number == number.to_integral():
            return str(int(number))
        return None
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r"(?:(?:chk|check)\s*[-#]?\s*)?(\d+)", normalize_text(value))
    return match.group(1) if match else None


def yes_no(value: Any) -> str | None:
    if value is True:
        return "y"
    if value is False:
        return "n"
    normalized = normalize_text(value)
    # A blank decision template or an applicability placeholder is not a decision.
    if re.match(r"^n\s*[/\.\-]\s*a\b", normalized) or re.match(
        r"^(yes|y|no|n)\s*(?:/|or|and)\s*(yes|y|no|n)\b", normalized
    ):
        return None
    match = re.match(r"^(yes|true|y|no|false|n)(?:\b|$)", normalized)
    if not match:
        return None
    return "y" if match.group(1) in {"y", "yes", "true"} else "n"


def identifier_matches(actual: Any, expected: Any) -> bool:
    # Public schedules use both "SINV 2026 reference 001" and "SINV-2026-001".
    # Normalize the literal word, retaining the complete prefix/year/serial.
    def public_reference(value: Any) -> Any:
        if not isinstance(value, str):
            return value
        return re.sub(
            r"\b([a-z]+)[ -]+(\d{4})[ -]+reference[ -]+(\d+)\b",
            r"\1-\2-\3",
            value,
            flags=re.IGNORECASE,
        )

    actual, expected = public_reference(actual), public_reference(expected)
    masked = re.fullmatch(r"\*+\s*(\d+)", normalize_text(expected))
    if masked:
        # A masked reference specifies the visible suffix, not the full number.
        # Keep this separate from exact GL, invoice, and other identifier keys.
        if isinstance(actual, (int, float, Decimal)):
            number = parse_number(actual)
            if number is None or number != number.to_integral():
                return False
            actual = str(int(number))
        actual_text = re.sub(r"(?<!\w)[x*•]+\s*(?=\d)", "", normalize_text(actual))
        actual_text = re.sub(r"(?<=\d)[ \t-]+(?=\d)", "", actual_text)
        numbers = re.findall(r"(?<![\w.])\d+(?![\w.])", actual_text)
        return any(number.endswith(masked.group(1)) for number in numbers)

    expected_number = parse_number(expected)
    if expected_number is not None and expected_number == expected_number.to_integral():
        token = str(int(expected_number))
        if len(token) >= 7:
            if isinstance(actual, (int, float, Decimal)):
                return parse_number(actual) == expected_number
            groups = re.findall(
                r"(?<![\w.])\d+(?:[ \t-]+\d+)*(?![\w.]|-\w)", normalize_text(actual)
            )
            return any(re.sub(r"[ \t-]", "", group) == token for group in groups)
        return token in _identifier_set(actual)
    else:
        token = normalize_text(expected).replace("*", "")
    actual_text = normalize_text(actual).replace("*", "")
    prefixed = re.fullmatch(r"([a-z]+)[ -]?(\d+)", token)
    if prefixed:
        prefix, digits = prefixed.groups()
        return (
            re.search(
                rf"(?<![a-z0-9-]){re.escape(prefix)}[ -]?{digits}(?![a-z0-9]|-\w)",
                actual_text,
            )
            is not None
        )
    return (
        bool(token)
        and re.search(rf"(?<![a-z0-9]){re.escape(token)}(?![a-z0-9])", actual_text)
        is not None
    )


def _identifier_set(value: Any) -> set[str]:
    return {str(int(token)) for token in re.findall(r"\d+", normalize_text(value))}


def _gl_account_set(value: Any) -> set[str]:
    text = normalize_text(str(value or "").replace("\n", ";"))
    if re.fullmatch(r"\d{4}(?:\s*,\s*\d{4})+", text):
        # Four-digit groups form an account list, not a thousands-formatted amount.
        return set(re.findall(r"\d{4}", text))
    # At the start of a journal entry, a direction followed by an account
    # number and description identifies the account. Drop its optional trailing
    # amount too, including whole-dollar amounts that resemble account numbers.
    text = re.sub(
        r"(^|[;|/])\s*(?:dr|cr|debit|credit)\s*:?\s*"
        r"(\d{4})\s+[a-z][a-z &()'’.\-]*?"
        r"(?:\s+\$?\s*[-+]?\d[\d,]*(?:\.\d+)?)?"
        r"(?=\s*(?:[;|/]|$))",
        r"\1\2",
        text,
    )
    directed_account = r"\b(?:dr|cr|debit|credit)\s*:?\s*(\d{4})\b"
    accounts = re.findall(directed_account, text)
    if accounts and re.fullmatch(r"[\s,;/]*", re.sub(directed_account, "", text)):
        # A field containing only directed account entries has no amount column.
        return set(accounts)
    # GL fields may include journal line numbers and debit/credit amounts.
    # Remove explicitly labelled amounts before extracting four-digit accounts.
    text = re.sub(
        r"\b(?:dr|cr|debit|credit)\s*:?\s*\$?\s*[-+]?\d[\d,]*(?:\.\d+)?",
        " ",
        text,
    )
    text = re.sub(r"\$\s*[-+]?\d[\d,]*(?:\.\d+)?", " ", text)
    text = re.sub(r"\bgl(?=\d{4}\b)", "gl ", text)
    return set(re.findall(r"(?<![\w.,$])\d{4}(?![\w.,%])", text))


def _words(value: Any) -> set[str]:
    return set(re.findall(r"[a-z]+", normalize_text(value)))


def _semantic_words(value: Any) -> set[str]:
    synonyms = {
        "loss": "result",
        "income": "result",
        "profit": "result",
        "earnings": "result",
        "recomputed": "recompute",
        "recomputation": "recompute",
        "reconciliation": "reconcile",
        "reconciled": "reconcile",
        "payments": "payment",
    }
    ignored = {
        "a",
        "an",
        "and",
        "at",
        "by",
        "for",
        "from",
        "gl",
        "in",
        "of",
        "on",
        "per",
        "sample",
        "selection",
        "the",
        "to",
        "activity",
    }
    return {synonyms.get(word, word) for word in _words(value) if word not in ignored}


def _asset_class(value: Any) -> str:
    text = re.sub(r"\s+[-–—]?\s*fa[ -]?\d+$", "", normalize_text(value))
    words = re.sub(r"[^a-z]+", " ", text.replace("&", " and ")).strip()
    aliases = {
        "vehicle": "vehicles",
        "leasehold improvement": "leasehold improvements",
        "furniture and fixture": "furniture and fixtures",
        "furnishings and fixture": "furniture and fixtures",
        "furnishings and fixtures": "furniture and fixtures",
        "furniture fixture": "furniture and fixtures",
        "furniture fixtures": "furniture and fixtures",
        "furnishings fixture": "furniture and fixtures",
        "furnishings fixtures": "furniture and fixtures",
    }
    return aliases.get(words, words)


def equivalent(
    actual: Any,
    expected: Any,
    *,
    comparison: str,
    tolerance: Decimal,
) -> bool:
    if comparison == "zero_or_blank":
        return (
            actual is None
            or (isinstance(actual, str) and actual.strip() in {"", "-", "–", "—"})
            or parse_number(actual) == Decimal(0)
        )
    if comparison == "date_in_text":
        expected_date = parse_date(expected)
        if expected_date is None:
            return False
        if isinstance(actual, (date, datetime)):
            return parse_date(actual) == expected_date
        month = r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
        tokens = re.findall(
            r"(?<!\d)(?:\d{4}-\d{1,2}-\d{1,2}|\d{1,2}/\d{1,2}/(?:\d{4}|\d{2})|"
            + month
            + r"\s+\d{1,2},?\s+\d{4})(?!\d)",
            str(actual),
            re.IGNORECASE,
        )
        dates = {
            parse_date(token)
            or parse_date(re.sub(r"(?<=\d)\s+(?=\d{4}$)", ", ", token))
            for token in tokens
        }
        # Repeated dates are fine; conflicting or invalid dates are not.
        return dates == {expected_date}
    if comparison == "fiscal_period":

        def year(value: Any) -> str | None:
            match = re.fullmatch(
                r"(?:fy\s*|fiscal\s+year\s+)?([12]\d{3})", normalize_text(value)
            )
            return match[1] if match else None

        wanted = year(expected)
        return wanted is not None and year(actual) == wanted
    if comparison == "reference_set":
        # Preserve complete document IDs (including prefixes); prose and order
        # are presentation choices. Numeric fragments alone are not identities.
        def references(value: Any) -> set[str]:
            text = str(value or "").casefold()
            for phrase, prefix in [
                (r"(?:customer\s+)?contract", "cc"),
                (r"purchase\s+order", "po"),
                (r"sales\s+order", "so"),
                (r"receiving\s+report", "rcv"),
            ]:
                text = re.sub(rf"\b{phrase}\s*[-#:]?\s*(?=\d)", prefix + "-", text)
            # Prefixes carry identity. Normalize spacing and leading zeros
            # without treating a different document type as the same reference.
            text = re.sub(r"\b([a-z]{1,8})\s*[-#:]?\s*(\d+(?:-\w+)*)\b", r"\1-\2", text)
            pattern = r"\b(?=[a-z0-9-]*\d)[a-z][a-z0-9]*(?:-[a-z0-9]+)+\b|\b(?=[a-z0-9]*\d)[a-z0-9]{6,}\b|\b\d+\b"
            return {
                "-".join(
                    str(int(part)) if part.isdigit() else part
                    for part in token.split("-")
                )
                for token in re.findall(pattern, text)
            }

        wanted = references(expected)
        return bool(wanted) and references(actual) == wanted
    if comparison == "name":

        def name_words(value: Any) -> set[str]:
            return _words(value) - {
                "inc",
                "incorporated",
                "llc",
                "ltd",
                "limited",
                "corp",
                "corporation",
            }

        return bool(name_words(expected)) and name_words(actual) == name_words(expected)
    if comparison == "date_range":

        def endpoints(value: Any) -> list[date | None]:
            parts = re.split(
                r"\s*(?:\bto\b|through|→|–|\s-\s)\s*", str(value), flags=re.IGNORECASE
            )
            return [parse_date(part) for part in parts]

        wanted = endpoints(expected)
        return len(wanted) == 2 and None not in wanted and endpoints(actual) == wanted
    if comparison == "check_reference":
        actual_reference = _check_reference(actual)
        return actual_reference is not None and actual_reference == _check_reference(
            expected
        )
    if comparison == "yn":
        actual_yn = yes_no(actual)
        return actual_yn is not None and actual_yn == yes_no(expected)
    if comparison == "identifier":
        return identifier_matches(actual, expected)
    if comparison == "payment_reference":
        # Payment columns mix check numbers with prefixed ACH identifiers.
        reference = _check_reference(expected)
        return (
            _check_reference(actual) == reference
            if reference is not None
            else identifier_matches(actual, expected)
        )
    if comparison == "required_words":
        expected_words = _words(expected)
        return bool(expected_words) and expected_words <= _words(actual)
    if comparison == "gl_account_set":
        expected_accounts = _gl_account_set(expected)
        return bool(expected_accounts) and _gl_account_set(actual) == expected_accounts
    if comparison == "row_label":
        # Flexible spelling/word order without fuzzy overlap between different
        # categories, e.g. capitalized inventory labor versus fixed-asset labor.
        words, identifiers = _semantic_words(expected), _identifier_set(expected)
        return (
            bool(words or identifiers)
            and words <= _semantic_words(actual)
            and identifiers <= _identifier_set(actual)
        )
    if comparison == "procedure_number":
        match = re.match(r"^\s*(\d+)\s*[.)]\s", str(actual or ""))
        return match is not None and int(match[1]) == int(expected)
    if comparison == "asset_class":
        return bool(_asset_class(expected)) and _asset_class(actual) == _asset_class(
            expected
        )
    if comparison not in {"auto", "text", "date", "numeric"}:
        raise ValueError(f"Unknown comparison: {comparison!r}")
    expected_date = parse_date(expected)
    if expected_date is not None:
        return parse_date(actual) == expected_date
    expected_number = parse_number(expected)
    if expected_number is not None:
        actual_number = parse_number(actual)
        return (
            actual_number is not None
            and abs(actual_number - expected_number) <= tolerance
        )
    fifo = {"fifo", "first in first out"}
    if normalize_text(expected).replace("-", " ") in fifo:
        return normalize_text(actual).replace("-", " ") in fifo
    return normalize_text(actual) == normalize_text(expected)
