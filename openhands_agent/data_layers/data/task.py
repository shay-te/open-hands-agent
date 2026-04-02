from openhands_agent.data_layers.data.record_field import RecordField


class Task:
    id = RecordField('id')
    summary = RecordField('summary')
    description = RecordField('description')
    branch_name = RecordField('branch_name')

    def __init__(
        self,
        id: str = '',
        summary: str = '',
        description: str = '',
        branch_name: str = '',
    ) -> None:
        self.id = id
        self.summary = summary
        self.description = description
        self.branch_name = branch_name

    def __repr__(self) -> str:
        return (
            f'Task(id={self.id!r}, summary={self.summary!r}, '
            f'description={self.description!r}, branch_name={self.branch_name!r})'
        )

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Task):
            return False
        return (
            self.id == other.id
            and self.summary == other.summary
            and self.description == other.description
            and self.branch_name == other.branch_name
        )
