from __future__ import annotations

from collections.abc import Mapping


def normalized_text(value: object) -> str:
    return str(value or '').strip()


def normalized_lower_text(value: object) -> str:
    return normalized_text(value).lower()


def condensed_text(value: object) -> str:
    return ' '.join(normalized_text(value).split())


def condensed_lower_text(value: object) -> str:
    return condensed_text(value).lower()


def alphanumeric_lower_text(value: object) -> str:
    return ''.join(character for character in normalized_lower_text(value) if character.isalnum())


def text_from_mapping(
    mapping: Mapping[object, object] | None,
    key: object,
    default: object = '',
) -> str:
    if not isinstance(mapping, Mapping):
        return normalized_text(default)
    return normalized_text(mapping.get(key, default))


def dict_from_mapping(mapping: object, key: object) -> dict:
    if not isinstance(mapping, Mapping):
        return {}
    value = mapping.get(key)
    return value if isinstance(value, dict) else {}


def list_from_mapping(mapping: object, key: object) -> list:
    if not isinstance(mapping, Mapping):
        return []
    value = mapping.get(key)
    return value if isinstance(value, list) else []


def text_from_attr(obj: object, attribute: str, default: object = '') -> str:
    return normalized_text(getattr(obj, attribute, default))
