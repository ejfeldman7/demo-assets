from __future__ import annotations

import re
from datetime import date
from typing import Iterable, List, Tuple



_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def validate_identifier(value: str) -> bool:
    """Return True if the identifier uses safe characters."""
    return bool(_IDENTIFIER_PATTERN.match(value or ""))


def parse_customer_ids(customer_ids_str: str) -> Tuple[bool, List[int] | str]:
    """
    Parse and validate customer IDs from string input.

    Supports: "1,2,3" or "1-10" or "1,2,5-8".
    """
    if not customer_ids_str or customer_ids_str.strip() == "":
        return True, []
    try:
        ids: List[int] = []
        parts = customer_ids_str.split(",")
        for part in parts:
            part = part.strip()
            if not part:
                continue
            if "-" in part:
                start_raw, end_raw = part.split("-", maxsplit=1)
                start = int(start_raw.strip())
                end = int(end_raw.strip())
                if end < start:
                    return False, f"Invalid range {part}"
                ids.extend(range(start, end + 1))
            else:
                ids.append(int(part))
        unique_ids = sorted(set(ids))
        return True, unique_ids
    except ValueError as exc:
        return False, f"Invalid customer ID format: {exc}"


def validate_dates(effective_date: date, expiration_date: date | None) -> Tuple[bool, str | None]:
    """Validate date logic for access rules."""
    if expiration_date and expiration_date <= effective_date:
        return False, "Expiration date must be after effective date."
    return True, None


def normalize_customer_ids(values: Iterable[int]) -> List[int]:
    """Return a sorted list of unique customer IDs."""
    return sorted(set(int(value) for value in values))


