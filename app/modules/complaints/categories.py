"""Request-only validation; historical stored categories remain unchanged."""
import json
from pathlib import Path
from typing import Annotated

from pydantic import AfterValidator, Field

CRIME_CATEGORIES = tuple(json.loads(
    (Path(__file__).resolve().parents[2] / 'frontend' / 'crime-categories.json').read_text(encoding='utf-8')))


def validate_category(value: str) -> str:
    value = value.strip()
    if value in CRIME_CATEGORIES[:-1]:
        return value
    if value.startswith('Other:'):
        description = value[6:].strip()
        if description and len(description) <= 143:
            return 'Other: ' + description
    raise ValueError('Select a listed crime category, or specify Other using "Other: " followed by 1–143 characters')


CrimeCategory = Annotated[str, Field(min_length=1, max_length=150), AfterValidator(validate_category)]
