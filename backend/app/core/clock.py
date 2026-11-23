from dataclasses import dataclass
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from app.core.config import DEFAULT_SANDBOX_BUSINESS_DATE

DEFAULT_SHOP_TIMEZONE = "Asia/Shanghai"


@dataclass(frozen=True)
class BusinessClock:
    """统一业务时钟，支持沙箱固定业务日或真实系统时间。"""

    sandbox_business_date: date | None = DEFAULT_SANDBOX_BUSINESS_DATE

    def business_date(self, timezone_name: str = DEFAULT_SHOP_TIMEZONE) -> date:
        if self.sandbox_business_date is not None:
            return self.sandbox_business_date
        return datetime.now(ZoneInfo(timezone_name)).date()

    def now(self, timezone_name: str = DEFAULT_SHOP_TIMEZONE) -> datetime:
        tz = ZoneInfo(timezone_name)
        if self.sandbox_business_date is not None:
            return datetime.combine(self.sandbox_business_date, time.min, tz)
        return datetime.now(tz)
