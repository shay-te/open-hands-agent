import ast
from pathlib import Path


DATA_ACCESS_DIR = Path(__file__).resolve().parents[1] / 'kato' / 'data_layers' / 'data_access'
CRUD_METHOD_NAMES = {'create', 'update'}
CRUD_BASE_CLASS = 'CRUDDataAccess'


def validate_data_access_crud_rule() -> list[str]:
    violations: list[str] = []

    for path in sorted(DATA_ACCESS_DIR.glob('*.py')):
        tree = ast.parse(path.read_text(), filename=str(path))

        for node in tree.body:
            if not isinstance(node, ast.ClassDef):
                continue

            base_names = {_get_base_name(base) for base in node.bases}
            method_names = {
                child.name
                for child in node.body
                if isinstance(child, ast.FunctionDef)
            }
            invalid_methods = sorted(CRUD_METHOD_NAMES & method_names)

            if invalid_methods and CRUD_BASE_CLASS not in base_names:
                violations.append(
                    f'{path.name}:{node.name} defines {", ".join(invalid_methods)} '
                    f'but does not inherit from {CRUD_BASE_CLASS}'
                )

    return violations


def _get_base_name(base: ast.expr) -> str:
    if isinstance(base, ast.Name):
        return base.id
    if isinstance(base, ast.Attribute):
        return base.attr
    return ''
