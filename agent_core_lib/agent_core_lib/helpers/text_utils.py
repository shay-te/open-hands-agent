from __future__ import annotations


def normalized_text(value: object) -> str:
    return str(value or '').strip()


def condensed_text(value: object) -> str:
    return ' '.join(normalized_text(value).split())


def text_from_attr(obj: object, attribute: str, default: object = '') -> str:
    return normalized_text(getattr(obj, attribute, default))


def text_from_mapping(mapping, key, default: object = '') -> str:
    """Read ``key`` from any ``.get(key, default)``-supporting object.

    Duck-typed on purpose — works for ``dict``, ``OmegaConf`` configs,
    ``SimpleNamespace(get=...)`` stand-ins in tests, and anything else
    that quacks like a mapping. ``None`` (and any other object without
    a usable ``.get``) yields the normalized default.
    """
    if mapping is None:
        return normalized_text(default)
    getter = getattr(mapping, 'get', None)
    if not callable(getter):
        return normalized_text(default)
    return normalized_text(getter(key, default))
