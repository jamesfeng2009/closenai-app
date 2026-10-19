"""同意（consent）台账表模型（Baseline §14.2）。

与 users 上的两个布尔位的分工：布尔位是**当前开关**（老 App 只读得到它，兼容期双写），
本表是**台账**——回答"哪个版本、哪一项、何时同意、何时撤回"。两个布尔位答不了这个问题。

`purpose` 的取值集合只有 `ConsentPurpose` 这一处定义（`test_consent_purposes.py` 按它
逐值对账 API 与代码里的字面量）。新增用途必须同时：加枚举值、写进 POST 白名单、
在 `PURPOSE_LINKED_FLAGS` 里登记对应的布尔位（没有布尔位就留空）。
"""
import enum
from datetime import datetime

from sqlalchemy import DateTime, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base


class ConsentPurpose(str, enum.Enum):
    """六种用途（Baseline §14.2）。值即落库串，改值等于造新台账，只能新增不能改。"""

    CORE_SERVICE = "core_service"          # 提供核心服务（随注册自动同意，撤回即注销口径）
    AI_PROCESSING = "ai_processing"        # AI 处理个人数据 ↔ users.ai_consent
    PROACTIVE_PUSH = "proactive_push"      # 主动消息推送 ↔ users.push_enabled
    ANALYTICS = "analytics"                # 匿名行为分析 ↔ users.analytics_consent
    DATA_EXPORT = "data_export"            # 通用数据导出（T1-01）
    MODEL_TRAINING = "model_training"      # 用我的数据训练模型（默认不提供，B-06）


# 落库值集合（顺序即 API 展示顺序，也是闸门逐值对账的清单）
CONSENT_PURPOSES: tuple[str, ...] = tuple(p.value for p in ConsentPurpose)

# 同意文案的版本号：文案改了就要 +1，用户对新版本重新表态（老版本的行保留）。
CONSENT_TEXT_VERSION = "v1"

# purpose → users 表兼容布尔位（双写目标）。None 表示该项没有布尔位可回写。
PURPOSE_LINKED_FLAGS: dict[str, str | None] = {
    ConsentPurpose.CORE_SERVICE.value: None,
    ConsentPurpose.AI_PROCESSING.value: "ai_consent",
    ConsentPurpose.PROACTIVE_PUSH.value: "push_enabled",
    ConsentPurpose.ANALYTICS.value: "analytics_consent",
    ConsentPurpose.DATA_EXPORT.value: None,
    ConsentPurpose.MODEL_TRAINING.value: None,
}


class UserConsent(Base):
    __tablename__ = "user_consents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), index=True)
    purpose: Mapped[str] = mapped_column(String(32))
    # 用户当时表态所依据的文案版本（CONSENT_TEXT_VERSION 的取值）
    version: Mapped[str] = mapped_column(String(20), default=CONSENT_TEXT_VERSION)
    # 授予/撤回时刻：granted_at 非空且 revoked_at 为空 == 当前有效。
    # 重复授予只刷新 granted_at 并清空 revoked_at（同 (user_id,purpose,version) 一行）。
    granted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint(
            "user_id", "purpose", "version", name="uq_user_consents_user_purpose_version"
        ),
    )
