from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Demo(Base):
    __tablename__ = "demo"

    name: Mapped[str] = mapped_column(String(50))


def test_base_common_fields_inherited() -> None:
    columns = Demo.__table__.columns
    assert "id" in columns
    assert "created_at" in columns
    assert "updated_at" in columns
    assert "is_deleted" in columns


def test_base_id_is_primary_key() -> None:
    assert Demo.__table__.columns["id"].primary_key


def test_base_soft_delete_default() -> None:
    assert Demo.__table__.columns["is_deleted"].default is None
    server_default = Demo.__table__.columns["is_deleted"].server_default
    assert server_default is not None
