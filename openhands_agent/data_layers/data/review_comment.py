from sqlalchemy import Column, Text, VARCHAR

from core_lib.data_layers.data.db.sqlalchemy.base import Base


class ReviewComment(Base):
    __tablename__ = 'review_comment'

    pull_request_id = Column(VARCHAR(length=255), primary_key=True)
    comment_id = Column(VARCHAR(length=255), primary_key=True)
    author = Column(VARCHAR(length=255), nullable=False)
    body = Column(Text, nullable=False)
