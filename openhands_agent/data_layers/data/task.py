from sqlalchemy import Column, Text, VARCHAR

from core_lib.data_layers.data.db.sqlalchemy.base import Base


class Task(Base):
    __tablename__ = 'task'

    id = Column(VARCHAR(length=255), primary_key=True)
    summary = Column(VARCHAR(length=255), nullable=False)
    description = Column(Text)
    branch_name = Column(VARCHAR(length=255), nullable=False)
