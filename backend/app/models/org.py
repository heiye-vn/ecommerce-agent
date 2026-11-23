from enum import StrEnum

from sqlalchemy import CheckConstraint, Enum, ForeignKey, ForeignKeyConstraint, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Role(StrEnum):
    ADMIN = "admin"
    OPERATOR = "operator"
    APPROVER = "approver"


class Organization(Base):
    __tablename__ = "organizations"

    name: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(20), server_default="active")


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(
            "(username IS NULL) = (password_hash IS NULL)", name="ck_users_credentials_pair"
        ),
        UniqueConstraint("username", name="uq_users_username"),
    )

    name: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(20), server_default="active")
    username: Mapped[str | None] = mapped_column(String(120))
    password_hash: Mapped[str | None] = mapped_column(String(255))


class Shop(Base):
    __tablename__ = "shops"
    __table_args__ = (UniqueConstraint("organization_id", "id", name="uq_shops_organization_id"),)

    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"))
    name: Mapped[str] = mapped_column(String(120))
    platform: Mapped[str] = mapped_column(String(32))
    timezone: Mapped[str] = mapped_column(String(64), server_default="Asia/Shanghai")
    status: Mapped[str] = mapped_column(String(20), server_default="active")


class Membership(Base):
    __tablename__ = "memberships"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "shop_id"], ["shops.organization_id", "shops.id"],
            name="fk_memberships_shop_organization",
        ),
        UniqueConstraint(
            "user_id", "organization_id", "shop_id", "role",
            name="uq_memberships_scope_role", postgresql_nulls_not_distinct=True,
        ),
    )

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"))
    shop_id: Mapped[int | None]
    role: Mapped[Role] = mapped_column(
        Enum(
            Role, name="membership_role", native_enum=False, create_constraint=True,
            values_callable=lambda roles: [role.value for role in roles],
        )
    )
