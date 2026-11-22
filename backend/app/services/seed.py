from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.core.security import PASSWORD_HASHER
from app.models import Membership, Organization, Role, Shop, User


DEMO_USERNAME = "ops.demo"
DEMO_ORGANIZATION = "本地业务沙箱"
DEMO_SHOP = "轻食旗舰店"


async def seed_demo_operator(session: AsyncSession, password: str) -> bool:
    await session.execute(text("SELECT pg_advisory_xact_lock(726001)"))
    if await session.scalar(select(User.id).where(User.username == DEMO_USERNAME)) is not None:
        return False

    organization = Organization(name=DEMO_ORGANIZATION)
    user = User(
        username=DEMO_USERNAME, name="沙箱运营",
        password_hash=await run_in_threadpool(PASSWORD_HASHER.hash, password),
    )
    session.add_all([organization, user])
    await session.flush()
    shop = Shop(organization_id=organization.id, name=DEMO_SHOP, platform="sandbox")
    session.add(shop)
    await session.flush()
    session.add(Membership(
        user_id=user.id, organization_id=organization.id, shop_id=shop.id, role=Role.OPERATOR,
    ))
    return True
