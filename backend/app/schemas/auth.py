from dataclasses import dataclass

from pydantic import BaseModel, Field

from app.models.org import Role


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=1, max_length=256, repr=False)


class UserInfo(BaseModel):
    id: int
    username: str
    name: str


class ShopAccess(BaseModel):
    id: int
    organization_id: int
    name: str
    timezone: str = "Asia/Shanghai"
    roles: list[Role]


class LoginResponse(BaseModel):
    access_token: str
    user: UserInfo
    shops: list[ShopAccess]


@dataclass(frozen=True)
class ShopContext:
    user_id: int
    shop: ShopAccess

    @property
    def id(self) -> int:
        return self.shop.id

    @property
    def organization_id(self) -> int:
        return self.shop.organization_id

    @property
    def name(self) -> str:
        return self.shop.name

    @property
    def timezone(self) -> str:
        return self.shop.timezone

    @property
    def roles(self) -> list[Role]:
        return self.shop.roles
