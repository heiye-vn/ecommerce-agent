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
    roles: list[Role]


class LoginResponse(BaseModel):
    access_token: str
    user: UserInfo
    shops: list[ShopAccess]
