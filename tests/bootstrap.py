import sys
import types
import typing


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
            self.session = types.SimpleNamespace(
                get=self._get,
                post=self._post,
                put=self._put,
                patch=self._patch,
                delete=self._delete,
            )

        def set_headers(self, headers: dict) -> None:
            self.headers = headers

        def set_timeout(self, timeout: int) -> None:
            self.timeout = timeout

        def set_auth(self, auth: dict) -> None:
            self.auth = auth

        def process_kwargs(self, **kwargs) -> dict:
            if self.headers:
                if 'headers' in kwargs:
                    kwargs['headers'] = {**kwargs['headers'], **self.headers}
                else:
                    kwargs['headers'] = self.headers
            kwargs['timeout'] = kwargs.get('timeout', self.timeout)
            if self.auth:
                kwargs['auth'] = self.auth
            return kwargs

        def _get(self, *args, **kwargs):
            raise NotImplementedError

        def _post(self, *args, **kwargs):
            raise NotImplementedError

        def _put(self, *args, **kwargs):
            raise NotImplementedError

        def _patch(self, *args, **kwargs):
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
    core_lib_connection_package = types.ModuleType("core_lib.connection")
    core_lib_connection_factory_module = types.ModuleType(
        "core_lib.connection.sql_alchemy_connection_factory"
    )
    core_lib_data_layers_module = types.ModuleType("core_lib.data_layers")
    core_lib_data_access_package = types.ModuleType("core_lib.data_layers.data_access")
    core_lib_data_access_module = types.ModuleType(
        "core_lib.data_layers.data_access.data_access"
    )
    core_lib_data_module = types.ModuleType("core_lib.data_layers.data")
    core_lib_data_helpers_module = types.ModuleType("core_lib.data_layers.data.data_helpers")
    core_lib_db_module = types.ModuleType("core_lib.data_layers.data.db")
    core_lib_sqlalchemy_module = types.ModuleType("core_lib.data_layers.data.db.sqlalchemy")
    core_lib_base_module = types.ModuleType("core_lib.data_layers.data.db.sqlalchemy.base")
    core_lib_service_package = types.ModuleType("core_lib.data_layers.service")
    core_lib_service_module = types.ModuleType("core_lib.data_layers.service.service")
    core_lib_rule_validator_package = types.ModuleType("core_lib.rule_validator")
    core_lib_rule_validator_module = types.ModuleType("core_lib.rule_validator.rule_validator")
    email_core_lib_package = types.ModuleType("email_core_lib")
    email_core_lib_module = types.ModuleType("email_core_lib.email_core_lib")

    class _Registry:
        def __init__(self) -> None:
            self._registered = {}

        def register(self, key, value) -> None:
            self._registered[key] = value

        def unregister(self, key) -> None:
            self._registered.pop(key, None)

        def registered(self):
            return list(self._registered.keys())

        def get(self, key):
            return self._registered[key]

    class _ConnectionFactoryRegistry(_Registry):
        def __init__(self) -> None:
            super().__init__()

        def get_or_reg(self, config):
            key = repr(config)
            if key not in self._registered:
                self._registered[key] = SqlAlchemyConnectionFactory(config)
            return self._registered[key]

    class SqlAlchemyConnectionFactory:
        def __init__(self, config) -> None:
            self.config = config

    class EmailCoreLib:
        def __init__(self, config) -> None:
            self.config = config
            self.send_calls = []

        def send(self, template_id, params, sender_info):
            self.send_calls.append((template_id, params, sender_info))
            return True

    class _CacheHandler:
        def flush_all(self) -> None:
            return None

    def build_url(
        protocol: str = None,
        username: str = None,
        password: str = None,
        host: str = None,
        port: str = None,
        path: str = None,
        file: str = None,
        *args,
        **kwargs,
    ) -> str:
        result = []
        if protocol:
            result.extend([protocol, '://'])
        if username or password:
            if username:
                result.append(username)
                if password:
                    result.append(f':{password}')
            result.append('@')
        if host:
            result.append(host)
        if port:
            result.append(f':{port}')
        if path:
            result.append(f'/{path.lstrip("/")}')
        if file:
            result.append(f'/{file.lstrip("/")}')
        return ''.join(result)

    class CoreLib:
        cache_registry = _Registry()
        observer_registry = _Registry()
        connection_factory_registry = _ConnectionFactoryRegistry()

        def __init__(self) -> None:
            self.logger = types.SimpleNamespace(info=lambda *args, **kwargs: None)
            self._core_lib_started = False

        def start_core_lib(self) -> None:
            self._core_lib_started = True

    CoreLib.cache_registry.register('test-cache', _CacheHandler())

    core_lib_core_module.CoreLib = CoreLib
    core_lib_data_helpers_module.build_url = build_url

    class Base:
        def __init__(self, **kwargs) -> None:
            for key, value in kwargs.items():
                setattr(self, key, value)

    class DataAccess:
        def __init__(self) -> None:
            return None

    class Service:
        def __init__(self) -> None:
            return None

    core_lib_base_module.Base = Base
    core_lib_data_access_module.DataAccess = DataAccess
    core_lib_service_module.Service = Service
    core_lib_connection_factory_module.SqlAlchemyConnectionFactory = (
        SqlAlchemyConnectionFactory
    )

    class ValueRuleValidator:
        def __init__(self, key: str, expected_type) -> None:
            self.key = key
            self.expected_type = expected_type

        def validate(self, data: dict) -> None:
            if self.key not in data:
                return

            value = data[self.key]
            if not isinstance(value, self.expected_type):
                expected_type_name = getattr(self.expected_type, '__name__', str(self.expected_type))
                raise ValueError(f'{self.key} must be {expected_type_name}')

    class RuleValidator:
        def __init__(self, validators: list[ValueRuleValidator]) -> None:
            self.validators = validators

        def validate(self, data: dict) -> None:
            for validator in self.validators:
                validator.validate(data)

    core_lib_rule_validator_module.ValueRuleValidator = ValueRuleValidator
    core_lib_rule_validator_module.RuleValidator = RuleValidator
    email_core_lib_module.EmailCoreLib = EmailCoreLib

    sys.modules["core_lib"] = core_lib_module
    sys.modules["core_lib.client"] = client_package
    sys.modules["core_lib.client.client_base"] = client_base_module
    sys.modules["core_lib.connection"] = core_lib_connection_package
    sys.modules[
        "core_lib.connection.sql_alchemy_connection_factory"
    ] = core_lib_connection_factory_module
    sys.modules["core_lib.jobs"] = jobs_package
    sys.modules["core_lib.jobs.job"] = job_module
    sys.modules["core_lib.core_lib"] = core_lib_core_module
    sys.modules["core_lib.data_layers"] = core_lib_data_layers_module
    sys.modules["core_lib.data_layers.data_access"] = core_lib_data_access_package
    sys.modules["core_lib.data_layers.data_access.data_access"] = core_lib_data_access_module
    sys.modules["core_lib.data_layers.data"] = core_lib_data_module
    sys.modules["core_lib.data_layers.data.data_helpers"] = core_lib_data_helpers_module
    sys.modules["core_lib.data_layers.data.db"] = core_lib_db_module
    sys.modules["core_lib.data_layers.data.db.sqlalchemy"] = core_lib_sqlalchemy_module
    sys.modules["core_lib.data_layers.data.db.sqlalchemy.base"] = core_lib_base_module
    sys.modules["core_lib.data_layers.service"] = core_lib_service_package
    sys.modules["core_lib.data_layers.service.service"] = core_lib_service_module
    sys.modules["core_lib.rule_validator"] = core_lib_rule_validator_package
    sys.modules["core_lib.rule_validator.rule_validator"] = core_lib_rule_validator_module
    sys.modules["email_core_lib"] = email_core_lib_package
    sys.modules["email_core_lib.email_core_lib"] = email_core_lib_module


def _install_sqlalchemy_stub() -> None:
    if "sqlalchemy" in sys.modules:
        return

    sqlalchemy_module = types.ModuleType("sqlalchemy")

    class _Type:
        def __init__(self, *args, **kwargs) -> None:
            self.args = args
            self.kwargs = kwargs

    class VARCHAR(_Type):
        pass

    class Text(_Type):
        pass

    class Column:
        def __init__(self, column_type, *args, **kwargs) -> None:
            self.column_type = column_type
            self.args = args
            self.kwargs = kwargs
            self.key = None

        def __set_name__(self, owner, name) -> None:
            self.key = name

        def __get__(self, instance, owner):
            if instance is None:
                return self
            return instance.__dict__.get(self.key)

        def __set__(self, instance, value) -> None:
            instance.__dict__[self.key] = value

    sqlalchemy_module.Column = Column
    sqlalchemy_module.VARCHAR = VARCHAR
    sqlalchemy_module.Text = Text
    sys.modules["sqlalchemy"] = sqlalchemy_module


def _install_omegaconf_stub() -> None:
    if "omegaconf" in sys.modules:
        return

    omegaconf_module = types.ModuleType("omegaconf")

    class DictConfig(dict):
        pass

    omegaconf_module.DictConfig = DictConfig
    sys.modules["omegaconf"] = omegaconf_module


def _install_hydra_stub() -> None:
    if "hydra" in sys.modules:
        return

    hydra_module = types.ModuleType("hydra")
    hydra_core_module = types.ModuleType("hydra.core")
    hydra_global_hydra_module = types.ModuleType("hydra.core.global_hydra")
    hydra_config_search_path_module = types.ModuleType("hydra.core.config_search_path")
    hydra_plugins_module = types.ModuleType("hydra.plugins")
    hydra_search_path_plugin_module = types.ModuleType("hydra.plugins.search_path_plugin")

    def main(*args, **kwargs):
        def decorator(func):
            return func
        return decorator

    class ConfigSearchPath:
        def append(self, provider: str, path: str) -> None:
            raise NotImplementedError

    class SearchPathPlugin:
        def manipulate_search_path(self, search_path: ConfigSearchPath) -> None:
            raise NotImplementedError

    class _GlobalHydraInstance:
        def clear(self) -> None:
            return None

    class GlobalHydra:
        _instance = _GlobalHydraInstance()

        @classmethod
        def instance(cls):
            return cls._instance

    hydra_config_search_path_module.ConfigSearchPath = ConfigSearchPath
    hydra_search_path_plugin_module.SearchPathPlugin = SearchPathPlugin
    hydra_global_hydra_module.GlobalHydra = GlobalHydra
    hydra_module.main = main

    sys.modules["hydra"] = hydra_module
    sys.modules["hydra.core"] = hydra_core_module
    sys.modules["hydra.core.global_hydra"] = hydra_global_hydra_module
    sys.modules["hydra.core.config_search_path"] = hydra_config_search_path_module
    sys.modules["hydra.plugins"] = hydra_plugins_module
    sys.modules["hydra.plugins.search_path_plugin"] = hydra_search_path_plugin_module


def _install_alembic_stub() -> None:
    if "alembic" in sys.modules:
        return

    alembic_module = types.ModuleType("alembic")
    alembic_command_module = types.ModuleType("alembic.command")
    alembic_config_module = types.ModuleType("alembic.config")

    class Config:
        def __init__(self) -> None:
            self.main_options = {}

        def set_main_option(self, key: str, value: str) -> None:
            self.main_options[key] = value

        def get_main_option(self, key: str, default=None):
            return self.main_options.get(key, default)

    def upgrade(config, revision: str) -> None:
        return None

    def downgrade(config, revision: str) -> None:
        return None

    def revision(config, message: str, autogenerate: bool = False) -> None:
        return None

    alembic_module.command = alembic_command_module
    alembic_module.config = alembic_config_module
    alembic_config_module.Config = Config
    alembic_command_module.upgrade = upgrade
    alembic_command_module.downgrade = downgrade
    alembic_command_module.revision = revision
    sys.modules["alembic"] = alembic_module
    sys.modules["alembic.command"] = alembic_command_module
    sys.modules["alembic.config"] = alembic_config_module


def _install_pydantic_stub() -> None:
    if "pydantic" in sys.modules:
        return

    pydantic_module = types.ModuleType("pydantic")

    class ValidationError(Exception):
        pass

    class BaseModel:
        def __init__(self, **data) -> None:
            hints = typing.get_type_hints(type(self), include_extras=True)
            for field, hint in hints.items():
                if field in data:
                    setattr(self, field, data[field])
                    continue

                if field in type(self).__dict__:
                    setattr(self, field, getattr(type(self), field))
                    continue

                if _is_optional_type(hint):
                    setattr(self, field, None)
                    continue

                raise ValidationError(f"missing field: {field}")

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


def _is_optional_type(annotation) -> bool:
    origin = typing.get_origin(annotation)
    if origin in {typing.Union, types.UnionType}:
        return type(None) in typing.get_args(annotation)
    return False


_install_core_lib_stubs()
_install_sqlalchemy_stub()
_install_omegaconf_stub()
_install_hydra_stub()
_install_alembic_stub()
_install_pydantic_stub()
