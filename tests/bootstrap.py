from __future__ import annotations

import sys
import types


def _install_core_lib_stubs() -> None:
    if "core_lib" in sys.modules:
        return

    core_lib_module = types.ModuleType("core_lib")

    client_package = types.ModuleType("core_lib.client")
    client_base_module = types.ModuleType("core_lib.client.client_base")

    class ClientBase:
        def __init__(self, base_url: str) -> None:
            self.base_url = base_url
            self.headers = None
            self.timeout = None
            self.auth = None

        def set_headers(self, headers: dict) -> None:
            self.headers = headers

        def set_timeout(self, timeout: int) -> None:
            self.timeout = timeout

        def set_auth(self, auth: dict) -> None:
            self.auth = auth

        def _get(self, *args, **kwargs):
            raise NotImplementedError

        def _post(self, *args, **kwargs):
            raise NotImplementedError

        def _put(self, *args, **kwargs):
            raise NotImplementedError

        def _delete(self, *args, **kwargs):
            raise NotImplementedError

    client_base_module.ClientBase = ClientBase

    jobs_package = types.ModuleType("core_lib.jobs")
    job_module = types.ModuleType("core_lib.jobs.job")

    class Job:
        def initialized(self, data_handler):
            raise NotImplementedError

        def set_data_handler(self, data_handler):
            self.initialized(data_handler)

        def run(self):
            raise NotImplementedError

    job_module.Job = Job

    core_lib_core_module = types.ModuleType("core_lib.core_lib")

    class CoreLib:
        def __init__(self) -> None:
            self.logger = types.SimpleNamespace(info=lambda *args, **kwargs: None)

    core_lib_core_module.CoreLib = CoreLib

    sys.modules["core_lib"] = core_lib_module
    sys.modules["core_lib.client"] = client_package
    sys.modules["core_lib.client.client_base"] = client_base_module
    sys.modules["core_lib.jobs"] = jobs_package
    sys.modules["core_lib.jobs.job"] = job_module
    sys.modules["core_lib.core_lib"] = core_lib_core_module


def _install_omegaconf_stub() -> None:
    if "omegaconf" in sys.modules:
        return

    omegaconf_module = types.ModuleType("omegaconf")

    class DictConfig(dict):
        pass

    omegaconf_module.DictConfig = DictConfig
    sys.modules["omegaconf"] = omegaconf_module


def _install_pydantic_stub() -> None:
    if "pydantic" in sys.modules:
        return

    pydantic_module = types.ModuleType("pydantic")

    class ValidationError(Exception):
        pass

    class BaseModel:
        def __init__(self, **data) -> None:
            for field in self.__annotations__:
                if field not in data:
                    raise ValidationError(f"missing field: {field}")
                setattr(self, field, data[field])

        @classmethod
        def model_validate(cls, data):
            if not isinstance(data, dict):
                raise ValidationError("payload must be a dict")
            return cls(**data)

        def __eq__(self, other) -> bool:
            return type(self) is type(other) and self.__dict__ == other.__dict__

    pydantic_module.BaseModel = BaseModel
    pydantic_module.ValidationError = ValidationError
    sys.modules["pydantic"] = pydantic_module


_install_core_lib_stubs()
_install_omegaconf_stub()
_install_pydantic_stub()
