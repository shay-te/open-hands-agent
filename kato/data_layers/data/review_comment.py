from kato.helpers.record_field_utils import RecordField


class ReviewComment:
    pull_request_id = RecordField('pull_request_id')
    comment_id = RecordField('comment_id')
    author = RecordField('author')
    body = RecordField('body')

    def __init__(
        self,
        pull_request_id: str = '',
        comment_id: str = '',
        author: str = '',
        body: str = '',
    ) -> None:
        self.pull_request_id = pull_request_id
        self.comment_id = comment_id
        self.author = author
        self.body = body

    def __repr__(self) -> str:
        return (
            'ReviewComment('
            f'pull_request_id={self.pull_request_id!r}, '
            f'comment_id={self.comment_id!r}, '
            f'author={self.author!r}, '
            f'body={self.body!r})'
        )

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, ReviewComment):
            return False
        return (
            self.pull_request_id == other.pull_request_id
            and self.comment_id == other.comment_id
            and self.author == other.author
            and self.body == other.body
        )
