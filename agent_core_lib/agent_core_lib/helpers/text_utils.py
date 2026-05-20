from __future__ import annotations

from collections.abc import Mapping


def normalized_text(value: object) -> str:
    return str(value or '').strip()


def condensed_text(value: object) -> str:
    return ' '.join(normalized_text(value).split())


def text_from_attr(obj: object, attribute: str, default: object = '') -> str:
    return normalized_text(getattr(obj, attribute, default))


def text_from_mapping(
    mapping: Mapping[object, object] | None,
    key: object,
    default: object = '',
) -> str:
    if not isinstance(mapping, Mapping):
        return normalized_text(default)
    return normalized_text(mapping.get(key, default))
