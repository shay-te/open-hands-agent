from __future__ import annotations

from abc import ABC, abstractmethod


class ValidationBase(ABC):
    @abstractmethod
    def validate(self, *args, **kwargs) -> None:
        raise NotImplementedError
