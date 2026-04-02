class RecordField:
    def __init__(self, key: str) -> None:
        self.key = key
        self._storage_name = f'_{key}'

    def __set_name__(self, owner, name) -> None:
        self._storage_name = f'_{name}'

    def __get__(self, instance, owner=None):
        if instance is None:
            return self
        return getattr(instance, self._storage_name, '')

    def __set__(self, instance, value) -> None:
        setattr(instance, self._storage_name, value)
