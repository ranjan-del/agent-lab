"""SQLAlchemy models.

Empty of tables by design: week 1's Wednesday builds the nine-table schema. This module
exists now so Alembic has a metadata object to autogenerate against from day one.
"""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Declarative base every model inherits from."""
