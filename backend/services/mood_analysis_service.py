"""
三层次情绪分析（KG-28，方案 §6.2）。

一句话定位：同一套数据、同一个 AI，按 `depth` 只换**上下文窗口 / 输出结构 / token 上限**，
不做三个人格（§6.2.2）。

这一层唯一对外承诺的是「每条结论都能指回用户自己的原话」，所以它的核心不是生成，
而是**逐字校验**：

1. 证据池只收用户自己写的行——记忆的 `source='self'` 且 `generation_run_id IS NULL`
   （AI 抽取的那批有 run 回链，见 O4），反思只收**不是 AI 起草**的那批
   （`metadata_json.source == "draft"` 排除；这条判据依赖
   `reflection_service.save` 把 source 写进 metadata，KG-28 一并补上那格）。
2. LLM 给出的每一句 `quote` 必须是某条池内原文的逐字子串（空白归一化 + casefold），
   否则整条丢掉。
3. 独立来源数不足（explore <1 / deep <2）⇒ **降级**：`pattern=null`、
   alternatives 清空、summary 换成只陈述可核对事实的模板。不补造解释——
   「没有证据时给一个听起来合理的模式」正是这类产品最贵的那种错误。
4. 轻松档（light）连降级都不需要：它按契约就不产出 pattern（0 条证据约束下的
   任何「模式」都是猜）。

危机与配额：
- 危机检测排在**配额之前**（`_crisis_screen`）：额度用尽的人正在说想伤害自己，
  回一句「今天次数用完了」是不可接受的。检测走 `crisis_intercept`（确定性词表，
  零 LLM、零留痕），命中即写 safety_events 并直接下发分地区热线文案。
- 配额是独立第三池，事实源为 `ai_generation_runs.purpose`（见 quota_service），
  `error` 那次不扣——用户没拿到任何产出。
"""
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from backend.ai.llm import llm
from backend.ai.safety import safety_service
from backend.config import settings
from backend.core import copy
from backend.core.errors import AppException, ErrorCode
from backend.core.time_utils import local_day_start_utc, utc_now
from backend.models.mood import MoodCheckin
from backend.models.schemas import (
    MoodAnalysisEvidence,
    MoodAnalysisNextStep,
    MoodAnalysisPattern,
    MoodAnalysisQuota,
    MoodAnalysisRequest,
    MoodAnalysisResponse,
)
from backend.models.user import User
from backend.repositories import (
    growth_repository,
    memory_repository,
    mood_repository,
    safety_repository,
    user_repository,
)
from backend.services.mood_service import mood_label_for
from backend.services.quota_service import quota_service

logger = logging.getLogger("closenai.mood_analysis")

# ── 三档差异（§6.2.2 的那张表，代码里只在这一处）────────────────────────
DEPTH_WINDOW_DAYS: Dict[str, int] = {"light": 1, "explore": 7, "deep": 30}
DEPTH_MATERIAL_LIMIT: Dict[str, int] = {"light": 3, "explore": 8, "deep": 20}
DEPTH_MAX_TOKENS: Dict[str, int] = {"light": 120, "explore": 300, "deep": 600}
# 需要的**独立来源**条数（不是引用条数：同一个 source 引两句仍算一条）。
DEPTH_MIN_SOURCES: Dict[str, int] = {"light": 0, "explore": 1, "deep": 2}
DEPTH_MAX_ALTERNATIVES: Dict[str, int] = {"light": 0, "explore": 1, "deep": 2}
# deep 用主模型（上下文最长、是实验里唯一要出读数的那档），light/explore 用 lite。
DEPTH_LITE: Dict[str, bool] = {"light": True, "explore": True, "deep": False}

EXCERPT_MAX_CHARS = 500
# 短于这个长度的「引用」几乎必然撞上（中文两个字、英文三个字母都能命中任何一段话），
# 它证明不了任何模式，只会给降级判定一个假的通过。
MIN_QUOTE_CHARS = 8
# 响应里最多带几条证据：卡片堆到第七张没人读，且证据面板要能一屏看完。
MAX_EVIDENCE_ITEMS = 4
NEXT_STEP_ACTIONS = ("reflect", "checkin", "goal", "none")

# purpose 必须与 quota_service.MOOD_ANALYSIS_PURPOSES 逐字相同（那侧按它数配额）。
_PURPOSE_PREFIX = "services.mood_analysis."

# 幂等窗口（§6.2.8）：同一 (user, request_id) 在窗口内重复点击返回同一结果，
# 不重复扣配额——免费档一天只有 1 次，双击把额度吃掉是这个实验最不该造的噪音。
# **进程内**缓存：生产 py-api 是 replicas=1（deploy/k8s/40-py-api.yaml:11）所以够用，
# 多副本或滚动更新期间不共享，重复请求会多扣一次；兜底是前端请求期间禁用按钮。
_IDEMPOTENCY_TTL_SECONDS = 600
_IDEMPOTENCY_MAX_ENTRIES = 256
_idempotency_cache: Dict[Tuple[str, str], Tuple[float, MoodAnalysisResponse]] = {}


def _normalize(text: str) -> str:
    """逐字校验用的归一化：所有空白压成单个空格 + casefold。

    只做这两件事是刻意的：不做标点归一、不做分词，否则「校验通过的引用」
    和用户屏幕上看到的那句就不是同一串字了。
    """
    return " ".join((text or "").split()).casefold()


def _parse_json_object(text: str) -> Optional[dict]:
    """从 LLM 输出里解析单个 JSON 对象（容错 markdown 围栏与前后噪声）。"""
    if not text:
        return None
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        data = json.loads(text[start:end + 1])
    except (json.JSONDecodeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


class MoodAnalysisService:
    """三层次情绪分析服务。"""

    async def analyze(
        self,
        db: AsyncSession,
        user_id: str,
        request: MoodAnalysisRequest,
        language: str = "en",
    ) -> MoodAnalysisResponse:
        """执行一次三层次分析。

        调用顺序本身是判据，不许「顺手整理」：
        今日签到（40402）→ 证据池 → **危机筛查** → 配额（40301）→ 生成 → 逐字校验。
        危机排在配额前，是为了额度耗尽时仍然把求助资源递出去。
        """
        # 开关兜底：路由层已在**任何业务判定之前**拦一次（关时不暴露功能存在），
        # 这里再判一次是因为服务层的失败方向应该是「不给结果」，而不是「猜调用方拦过了」。
        if not settings.mood_analysis_depths_enabled:
            raise AppException(ErrorCode.RESOURCE_NOT_FOUND, "资源不存在")

        depth = request.depth
        user = await user_repository.require_user(db, user_id)
        day_start = local_day_start_utc(user.timezone)

        # 1. 只分析今天的签到（请求里没有 checkin_id，见 schemas 的注释）
        checkin = await mood_repository.get_today(db, user_id, day_start)
        if checkin is None:
            raise AppException(ErrorCode.RESOURCE_NOT_FOUND, "今天还没有签到，先记一下情绪")

        # 2. 证据池（用户自己写的行）
        materials = await self._collect_materials(db, user, day_start, depth)

        # 3. 危机筛查：focus + 今日原文，确定性词表，命中即出、不调 LLM、不扣额度
        crisis = self._crisis_screen(user, request.focus, materials, language)
        if crisis is not None:
            await safety_repository.record(
                db, user.id, kind=crisis["kind"], source="mood_analysis",
                matched_terms=crisis["matched"], hotline=crisis["hotline"],
                language=language, excerpt=(request.focus or ""),
                advice_blocked=crisis["advice_blocked"],
            )
            return self._crisis_response(depth, crisis, language)

        # 4. 幂等（§6.2.8）：同一 request_id 在窗口内不再跑第二次，也不重复扣额度
        cached = self._cached(user_id, request.request_id)
        if cached is not None:
            return cached

        # 5. 配额（第三池）：必须原子预占，不能先查后写——留痕落库前有一个空窗，
        #    并发请求会读到同一个 used 而全部通过（P2 #17）。
        if not await quota_service.reserve_mood_analysis(db, user):
            raise AppException(ErrorCode.QUOTA_EXCEEDED, "今天的分析次数已经用完，请明天再来")

        try:
            payload = await self._generate(checkin, materials, request, depth, language)
            response = await self._build_response(
                db, user, checkin, materials, depth, language, payload
            )
        finally:
            await quota_service.release_mood_analysis(user)
        self._remember(user_id, request.request_id, response)
        return response

    # ── 证据池 ──────────────────────────────────────────────────────

    async def _collect_materials(
        self, db: AsyncSession, user: User, day_start: datetime, depth: str
    ) -> List[Dict[str, Any]]:
        """收集可作为逐字校验依据的用户原文。

        返回元素：`{"source_type", "source_id", "excerpt", "occurred_at"}`，
        按时间倒序、截到该档上限；excerpt 截到 EXCERPT_MAX_CHARS。
        """
        window_start = day_start - timedelta(days=DEPTH_WINDOW_DAYS[depth] - 1)
        window_end = day_start + timedelta(days=1)
        limit = DEPTH_MATERIAL_LIMIT[depth]

        rows: List[Dict[str, Any]] = []
        # 用户自己写的记忆（含签到备注那条沉淀路径）
        memories = await memory_repository.list_own_words(
            db, user.id, window_start, window_end, limit=limit
        )
        for m in memories:
            rows.append({
                "source_type": "memory",
                "source_id": m.id,
                "excerpt": (m.content or "")[:EXCERPT_MAX_CHARS],
                "occurred_at": _aware(m.created_at),
            })

        # 用户保存下来的反思（growth_records 里 record_type=reflection 那一类）
        records = await growth_repository.list_recent(
            db, user.id, record_type="reflection",
            days=DEPTH_WINDOW_DAYS[depth], limit=limit,
        )
        for r in records:
            # 「AI 写的话」不能回头被当成「你自己的原话」引用，否则深潜档会把上一轮
            # 模型自己生成的句子指认成用户的表达。判据与 KG-30 金句卡**同一档**
            # （share_service 里 draft/mood_analysis 都算 AI 起草）——两处口径不一样的话，
            # 同一句话会在一张卡上写「不是 AI 替你写的」、在另一屏被引成「你的原话」。
            # `prompt` 不在这一档：那是用户照着提示自己敲的正文。
            if _reflection_source(r) in ("draft", "mood_analysis"):
                continue
            rows.append({
                "source_type": "reflection",
                "source_id": r.id,
                "excerpt": (r.content or "")[:EXCERPT_MAX_CHARS],
                "occurred_at": _aware(r.created_at),
            })

        rows.sort(key=lambda item: item["occurred_at"], reverse=True)
        return rows[:limit]

    # ── 危机 ────────────────────────────────────────────────────────

    def _crisis_screen(
        self,
        user: User,
        focus: Optional[str],
        materials: List[Dict[str, Any]],
        language: str,
    ) -> Optional[dict]:
        """确定性危机词表筛查（复用 §5.16 ④ 那一条闸，不另起一套词表）。

        输入 = 用户这次打的 focus + 池内的原文。`crisis_intercept` 本身不调模型，
        所以这一步既不花钱也不花额度。
        """
        text = "\n".join(
            [t for t in ((focus or "").strip(), *(m["excerpt"] for m in materials)) if t]
        )
        return safety_service.crisis_intercept(text, language, user.timezone)

    def _crisis_response(
        self, depth: str, crisis: dict, language: str
    ) -> MoodAnalysisResponse:
        """危机出口：直接下发分地区热线文案，不给 summary／pattern／证据。

        `reply` 已由 copy.safety.crisis.{kind} 把热线号码渲染进去，
        所以前端只需要把这段原样显示——不要在客户端再拼一个号码（那是第二份事实源）。
        """
        return MoodAnalysisResponse(
            analysis_id=str(uuid4()),
            depth=depth,
            created_at=utc_now(),
            summary=crisis["reply"],
            pattern=None,
            alternatives=[],
            evidence=[],
            next_step=MoodAnalysisNextStep(
                text=copy.t("mood", "analysis.crisis_next_step", language),
                action="none",
            ),
            safety_status="crisis",
            generation_run_id=None,
            quota=None,
        )

    # ── 生成 ────────────────────────────────────────────────────────

    async def _generate(
        self,
        checkin: MoodCheckin,
        materials: List[Dict[str, Any]],
        request: MoodAnalysisRequest,
        depth: str,
        language: str,
    ) -> Optional[dict]:
        """调一次 LLM，返回解析后的 JSON；解析/调用失败返回 None（→ 降级）。"""
        if not materials and depth != "light":
            # 没有一句原文可引用时，explore/deep 的逐字校验必然失败；
            # 先花一次 LLM 再降级只是把钱烧在一个已知的答案上。
            return None
        prompt = self._build_prompt(checkin, materials, request, depth, language)
        try:
            text = await llm.complete(
                prompt,
                language=language,
                lite=DEPTH_LITE[depth],
                max_tokens=DEPTH_MAX_TOKENS[depth],
                temperature=0.5,
                purpose=_PURPOSE_PREFIX + depth,
            )
        except Exception as exc:  # noqa: BLE001  失败不抛给用户，走降级出口
            logger.warning("mood_analysis LLM 失败 depth=%s: %s", depth, exc)
            return None
        payload = _parse_json_object(text)
        if payload is None:
            logger.warning("mood_analysis 输出不可解析 depth=%s", depth)
        return payload

    def _build_prompt(
        self,
        checkin: MoodCheckin,
        materials: List[Dict[str, Any]],
        request: MoodAnalysisRequest,
        depth: str,
        language: str,
    ) -> str:
        """按档位拼 prompt。

        三档**共用同一段素材编号**，只有输出契约不同——这样「换一档重看」时
        模型看到的世界是一样的，实验里变的那一格才是深度本身。
        """
        label = mood_label_for(checkin.mood_emoji, language)
        lines = [
            f"Today's mood check-in: {checkin.mood_emoji} ({label}).",
        ]
        if request.focus and request.focus.strip():
            lines.append(f"The user asked to focus on: {request.focus.strip()}")
        if materials:
            lines.append("The user's OWN words (each line is one numbered source):")
            for i, m in enumerate(materials):
                lines.append(f"[{i}] ({m['source_type']}) {m['excerpt']}")
        else:
            lines.append("No saved text from the user yet.")

        contract = {
            "light": (
                'Return {"summary": "<one warm sentence, <=40 words>", '
                '"question": "<one open question inviting the user to say more>", '
                '"quotes": []}'
            ),
            "explore": (
                'Return {"summary": "<2-3 sentences of observable fact>", '
                '"pattern": {"description": "<one possible pattern, hedged language>", '
                '"quote": "<VERBATIM substring of one numbered source>", "confidence": 0.0}, '
                '"alternatives": ["<one different reading of the same facts>"], '
                '"quotes": []}'
            ),
            "deep": (
                'Return {"summary": "<2-3 sentences of observable fact>", '
                '"pattern": {"description": "<one possible pattern, hedged language>", '
                '"quote": "<VERBATIM substring of one numbered source>", "confidence": 0.0}, '
                '"alternatives": ["<reading A>", "<reading B>"], '
                '"quotes": ["<VERBATIM substring of a DIFFERENT numbered source>"], '
                '"next_step": {"text": "<one small concrete step>", '
                '"action": "reflect|checkin|goal|none"}}'
            ),
        }[depth]

        return (
            "You are reflecting a person's own words back to them. Observe only: "
            "no diagnosis, no predictions, no medicalized or astrological wording.\n"
            f"{safety_service.boundary(language)}\n"
            f"{safety_service.evidence_policy(language)}\n"
            "Every `quote` you return MUST be an exact, verbatim substring of one of the "
            "numbered sources above — copy it character for character, do not paraphrase, "
            "translate, or tidy punctuation. Quotes that are not exact substrings are "
            "discarded along with whatever they were meant to support.\n"
            f"{contract}\n"
            "Output ONLY the JSON object, no prose, no markdown fence.\n"
            + "\n".join(lines)
        )

    # ── 校验与组装 ──────────────────────────────────────────────────

    async def _build_response(
        self,
        db: AsyncSession,
        user: User,
        checkin: MoodCheckin,
        materials: List[Dict[str, Any]],
        depth: str,
        language: str,
        payload: Optional[dict],
    ) -> MoodAnalysisResponse:
        """逐字校验 → 达标给 pattern，不达标降级；最后附上本池用量。

        两条容易被「顺手优化」掉的判据，写在这里防止下一次整理：
        ① pattern 只有在**它自己的那句 quote** 命中时才保留——别的引用命中了不能替它背书；
        ② 独立来源计数看 `source_id`，同一行引两句在深潜档里仍算一条（§6.2.2 要的是「独立」）。
        """
        index = {_normalize(m["excerpt"]): m for m in materials}
        summary = str((payload or {}).get("summary") or "").strip()
        raw_pattern = (payload or {}).get("pattern")
        raw_pattern = raw_pattern if isinstance(raw_pattern, dict) else {}

        pattern_quote = str(raw_pattern.get("quote") or "").strip()
        pattern_material = self._match_quote(pattern_quote, index) if pattern_quote else None

        hits: List[Tuple[Dict[str, Any], str]] = []
        if pattern_material is not None:
            hits.append((pattern_material, pattern_quote))
        for quote in self._extra_quotes(payload):
            matched = self._match_quote(quote, index)
            if matched is None or any(h[0]["source_id"] == matched["source_id"] for h in hits):
                continue
            hits.append((matched, quote))

        # 轻松档按契约不产出 pattern（0 条证据约束下的任何「模式」都是猜）
        pattern = None
        if (
            depth != "light"
            and pattern_material is not None
            and len(hits) >= DEPTH_MIN_SOURCES[depth]
        ):
            pattern = self._pattern_from(raw_pattern)
        degraded = payload is None or (depth != "light" and pattern is None)

        # 出口安全闸：正文是断言型的（它在说「你可能……」），走 scope="assertion"。
        # 被拦＝这条不能出，整档降级，而不是换个说法继续推。
        if not degraded and summary:
            summary = await safety_service.check_or(summary, language, "", scope="assertion")
            if not summary:
                degraded = True

        if degraded or not summary:
            summary = self._factual_summary(checkin, materials, language)
            pattern = None
            degraded = True

        alternatives = [] if pattern is None else self._alternatives(payload, depth)
        evidence = [
            MoodAnalysisEvidence(
                source_type=m["source_type"],
                source_id=m["source_id"],
                excerpt=m["excerpt"],
                occurred_at=m["occurred_at"],
            )
            for m, _q in hits[:MAX_EVIDENCE_ITEMS]
        ]
        next_step = None if degraded else self._next_step(payload, depth)

        return MoodAnalysisResponse(
            analysis_id=str(uuid4()),
            depth=depth,
            created_at=utc_now(),
            summary=summary,
            pattern=pattern,
            alternatives=alternatives,
            evidence=evidence,
            next_step=next_step,
            safety_status="degraded" if degraded else "ok",
            generation_run_id=llm.current_run_id(),
            # 留痕行由 record_run 在自己的会话里即时提交，所以这里读到的 used 含本次
            quota=await quota_service.mood_analysis_quota(db, user),
        )

    @staticmethod
    def _extra_quotes(payload: Optional[dict]) -> List[str]:
        """除 pattern.quote 之外模型自己交上来的引用列表。"""
        raw = (payload or {}).get("quotes") or []
        if not isinstance(raw, list):
            return []
        return [str(item).strip() for item in raw if str(item or "").strip()]

    @staticmethod
    def _match_quote(
        quote: str, index: Dict[str, Dict[str, Any]]
    ) -> Optional[Dict[str, Any]]:
        """逐字命中判定：归一化后的引用是哪条池内原文的子串。

        命中多条时取**池内顺序**的第一条（新材料优先，因为 `_collect_materials` 已按时间倒序）；
        太短的引用（<MIN_QUOTE_CHARS）直接判不命中，它证明不了任何模式。
        """
        needle = _normalize(quote)
        if len(needle) < MIN_QUOTE_CHARS:
            return None
        for normalized, material in index.items():
            if needle in normalized:
                return material
        return None

    @staticmethod
    def _pattern_from(raw_pattern: dict) -> Optional[MoodAnalysisPattern]:
        description = str(raw_pattern.get("description") or "").strip()
        if not description:
            return None
        try:
            confidence = float(raw_pattern.get("confidence"))
        except (TypeError, ValueError):
            confidence = 0.0
        return MoodAnalysisPattern(
            description=description,
            # 模型给越界值时夹紧而不是报错：这一格是展示用的把握程度，
            # 为它整档降级不值得；但响应里绝不能出现 1.4 这种数。
            confidence=min(max(confidence, 0.0), 1.0),
        )

    @staticmethod
    def _alternatives(payload: Optional[dict], depth: str) -> List[str]:
        raw = (payload or {}).get("alternatives") or []
        if not isinstance(raw, list):
            return []
        items = [str(a).strip() for a in raw if str(a or "").strip()]
        return items[:DEPTH_MAX_ALTERNATIVES[depth]]

    @staticmethod
    def _next_step(payload: Optional[dict], depth: str) -> Optional[MoodAnalysisNextStep]:
        """light 用 `question` 当下一步（action=reflect），explore/deep 用 `next_step`。"""
        if depth == "light":
            question = str((payload or {}).get("question") or "").strip()
            if not question:
                return None
            return MoodAnalysisNextStep(text=question, action="reflect")
        raw = (payload or {}).get("next_step")
        if not isinstance(raw, dict):
            return None
        text = str(raw.get("text") or "").strip()
        if not text:
            return None
        action = str(raw.get("action") or "none").strip().lower()
        return MoodAnalysisNextStep(
            text=text, action=action if action in NEXT_STEP_ACTIONS else "none"
        )

    def _factual_summary(
        self, checkin: MoodCheckin, materials: List[Dict[str, Any]], language: str
    ) -> str:
        """降级出口：只说可核对的事实（今天的 emoji + 池内原文条数），不含任何解释。

        模板来自文案库 `mood.analysis.*`（15 语言），不在代码里硬编码中文——
        这条降级话是要给美国用户看的，写死一种语言就等于在非中文界面漏一句中文。
        """
        facts = copy.t("mood", "analysis.facts", language).format(
            label=mood_label_for(checkin.mood_emoji, language),
            count=len(materials),
        )
        return copy.t("mood", "analysis.degraded_summary", language).format(facts=facts)

    # ── 幂等缓存 ────────────────────────────────────────────────────

    def _cached(self, user_id: str, request_id: Optional[str]) -> Optional[MoodAnalysisResponse]:
        if not request_id:
            return None
        entry = _idempotency_cache.get((user_id, request_id))
        if entry is None:
            return None
        stored_at, response = entry
        if time.monotonic() - stored_at > _IDEMPOTENCY_TTL_SECONDS:
            _idempotency_cache.pop((user_id, request_id), None)
            return None
        return response

    def _remember(
        self, user_id: str, request_id: Optional[str], response: MoodAnalysisResponse
    ) -> None:
        if not request_id:
            return
        now = time.monotonic()
        stale = [
            key for key, (stamp, _) in _idempotency_cache.items()
            if now - stamp > _IDEMPOTENCY_TTL_SECONDS
        ]
        for key in stale:
            _idempotency_cache.pop(key, None)
        if len(_idempotency_cache) >= _IDEMPOTENCY_MAX_ENTRIES:
            # 满了就丢最旧的一条：缓存命中的收益是「少扣一次额度」，
            # 丢掉最旧不影响正确性，无界增长才会（这是常驻进程）。
            oldest = min(_idempotency_cache.items(), key=lambda kv: kv[1][0])[0]
            _idempotency_cache.pop(oldest, None)
        _idempotency_cache[(user_id, request_id)] = (now, response)


def _aware(value: Optional[datetime]) -> datetime:
    """把 SQLite 读回来的 naive datetime 标成 UTC。"""
    if value is None:
        return utc_now()
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _reflection_source(record: Any) -> Optional[str]:
    """从 growth_records.metadata_json 里读反思来源（`draft`＝AI 起草后用户保存）。

    metadata 缺失或不可解析时返回 None＝按「用户自己的」处理：这一格影响的是
    **能不能被当作用户原话引用**，历史数据里没有留痕的绝大多数是手打的，
    把它们全排除会让深潜档在老用户身上永远没有材料。
    """
    raw = getattr(record, "metadata_json", None)
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    value = data.get("source")
    return str(value) if value else None


mood_analysis_service = MoodAnalysisService()
