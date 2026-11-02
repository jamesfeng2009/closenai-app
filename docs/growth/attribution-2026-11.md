# KG-20 · UTM / 深链归因规范（2026-11-02）

对应任务：`docs/keyword-growth-task-board-2026-11-01.md` 的 KG-20（D 测量，P0）。
验收口径：UTM 分类 + App Store campaign link + 测试矩阵；URL 不含 PII；归因窗口写明。

实现：[frontend/src/services/attribution.ts](../frontend/src/services/attribution.ts)
闸：[frontend/__tests__/attribution.test.ts](../frontend/__tests__/attribution.test.ts)（31 条）
上游决策：`docs/keyword-growth-implementation-plan-2026-11-01.md` D2（不新建表）、D6（不扩埋点属性键）。

---

## 1. 这份归因**只做**什么、明确不做什么

| 面 | 做 | 不做，以及为什么 |
|---|---|---|
| 解析 | 品牌域唤起 URL 里的 `utm_*` 五键、App Store campaign link 的 `pt/ct/mt`、落地路径 | 不解析推荐码——`referral.ts:42-48` 是唯一入口，两处解析必漂移 |
| 清洗 | 解码 → 小写 → 字符集闸 → PII 闸，单键不过关只丢那一个键 | 不做截断保留：截断后的邮箱前缀仍然是个人信息 |
| 留存 | 本机 AsyncStorage 键 `@closenai/attribution`，首触不覆盖、最近一次覆盖 | **不上送**：见 §2 |
| 读数 | 供月度复盘（KG-22）本地导出诊断行 `attributionForDiagnostics()` | 不充当渠道装机量：见 §5 天花板 |

## 2. 为什么今天不上送（这是设计，不是偷懒）

1. **埋点属性白名单是两侧同源的死表**：`analytics.ts:19-42` 与
   `backend/services/analytics_service.py:36-63` 是同一 22 个键
   （`screen / screen_from / screen_to / section / feature / action / source / trigger / result /
   reason / duration_ms / count / value / tier / language / platform / app_version / locale /
   is_new_user / streak_count / relationship_level`）。
   `utm_*` 不在其中，两端 `sanitize` / `anonymize_properties` 会**静默丢弃**。
   加一个 `utm_campaign` 等于同时改前端闸、后端闸和 PostHog 面口径，属决策 D6 明令不做的事。
2. **服务端没有落点**：没有安装来源列（渠道切片今天只能取 `push_tokens.platform`），
   而决策 D2 规定不新建表。上送也没有地方存。
3. **渠道量另有权威口径**：`docs/metrics-dictionary.md` 的「关键词渠道七格」第 2～3 格
   写明 `store_page_view` / `install` 取自 App Store Connect 与 Play Console，
   分群靠 campaign link 的 `pt/ct/mt`，不靠客户端自报。

**将来若要上送**（不在本任务范围）：先加服务端列 + 扩两侧白名单 + 隐私政策同步，三件一起做。

## 3. UTM 分类规范

放行的键只有五个，取值规则统一：小写字母数字开头，允许 `._+-`，长度 ≤48，
命中 PII 形状即整键丢弃。**中文渠道名不进 URL**（字符集挡得住，就别指望它当读数维度）。

| 键 | 含义 | 取值口径 | 例 |
|---|---|---|---|
| `utm_source` | 流量来自哪个平台/站点 | 平台名小写，一个平台一个值，不许带活动信息 | `reddit`、`podcast_transcript`、`google` |
| `utm_medium` | 形式 | 固定小集合：`social`、`cpc`、`email`、`affiliate`、`referral`、`organic` | `social` |
| `utm_campaign` | 一次活动 | `年月-活动短名`，年份用 `nov26` 这种缩写，**不许写完整日期串** | `nov26-sale` |
| `utm_term` | 搜索词 | 仅搜索广告用；一词一值 | `mood_tracker` |
| `utm_content` | 同活动的创意区分 | 单字母或 `a1`/`b2` 这类短形 | `b` |

`utm_kludge_id`、`fbclid`、`gclid` 这类第三方自动追加的参数**不采集**：
`UTM_KEYS` 是 allowlist（`attribution.ts:28-34`），未知参数即使带了值也不会落盘。

完整日期串为什么要禁：`20261101_sale` 含连续 8 位数字，与手机号/证件号的形状无法区分。
清洗规则选择整键丢弃（`attribution.ts:81-87`，其中第 83 行是 `\d{7,}`），代价是这类活动名收不到，
收益是永远不会把一个 11 位数字当渠道名存进用户手机。渠道命名请改 `nov26-sale`。

## 4. App Store campaign link

- 令牌：`pt`（投放来源类型）、`ct`（活动令牌）、`mt`（媒体类型）。三者都是可选，
  解析位置与 `utm_*` 同一条查询串，`attribution.ts:37`。
- **本文件不复制 `pt`/`mt` 的取值表**：Apple 的取值集合会变，抄进文档就会变成一条无法核验的承诺。
  投放侧在 App Store Connect → 营销工具 → 活动链接里生成，把成品链接回填到
  `docs/aso/` 的投放表（KG-04/KG-05 的文件），本文只认键名与清洗规则。
- campaign link 落在**品牌域**的链接上才作数（`attribution.ts:62` 是域常量，`:93-96` 是判定）；
  非品牌域一律判 null，理由与推荐码同源：任何能唤起 App 的链接都不该有权给自己贴渠道标签
  （`referral.ts:38-40`）。

## 5. 归因窗口与天花板

| 口径 | 规则 | 依据 |
|---|---|---|
| 首触（first-touch） | `first` 写入后不再覆盖，直到 `clearAttribution()` | `attribution.ts:222-236` |
| 最近触（last-touch） | 每次有效唤起覆盖 `last`，与推荐码的「后到代表最新来源」一致 | `attribution.ts:228-234`、`referral.ts:56-59` |
| 唤起计数 | `captures` 只数**本机看到过几次带归因的唤起** | `attribution.ts:232` |
| 保留期 | 本机长期保留，直到手工清除或账号删除流程覆盖 | 未接线，见 §7 |
| 商店侧窗口 | `install` 一格的渠道归因窗口以商店后台口径为准（周窗口 + 商店自身归因期），不在本机 | `docs/metrics-dictionary.md` 第 3 格 |

**天花板（与 `referral.ts:10-15` 同一条断点，不靠小聪明绕）**：未安装用户从落地页跳商店、
装完再打开，URL 参数过不了 App Store——Apple 不向第三方 App 透传任意参数。
因此本机拿到的是**下界**：只有「已安装 → Universal Link / Deep Link 唤起」这条路径归因完整。
不许用这份数据反推某渠道的装机数，也不许把它当分享/投放总量。

## 6. 测试矩阵（31 条，逐条可打红）

| 组 | 判据 | 用例数 | 打红方式 |
|---|---|---|---|
| 解析 | 品牌域五键 + 落地路径一起收 | 1 | 少收一键 / 键名漂 |
| 解析 | campaign 只认 `pt/ct/mt` | 1 | 加认 `utm_campaign_2` |
| 解析 | 未知 `utm_*` 变体与 `fbclid` 忽略 | 1 | 改成「未知也留」 |
| 解析 | 非品牌域与伪装域（`closenai.app.evil.example`）判 null | 1 | 去掉 `isBrandHost` |
| 解析 | 品牌子域放行 | 1 | 改成全等匹配 |
| 解析 | 无 UTM 无 campaign 返回 null，不写盘 | 2 | 放行裸域名 |
| 清洗 | `%20`、`+`、大小写、首尾空格归一 | 1 | 去掉解码或归一 |
| 清洗 | 中文/emoji/逗号/冒号/空值判 null | 1 | 放宽字符集 |
| 清洗 | 48 字符边界（放行 48、挡 49） | 1 | 去掉长度上限 |
| 清洗 | 畸形百分号转义判 null | 1 | try/catch 里回退成原值 |
| 清洗 | 单键脏不连坐 | 1 | 改成整条丢弃 |
| PII | 邮箱 / ≥7 位连续数字 / 分段电话 / hex 长串 / base64 段 / 嵌套 URL 全丢 | 6 | 任一条正则删掉 |
| PII | **正向证人**：`nov26-sale`、`podcast_s02e11`、`b2` 必须放行 | 1 | 把规则改成全挡 |
| PII | PII 值进不了落盘（整份 JSON 查不到邮箱与数字串） | 1 | 只挡显示不挡写入 |
| 路径 | 规范化、剥查询串与片段、拒绝穿越与中文段 | 1 | 去掉路径字符集 |
| 落盘 | 首触不覆盖、last 覆盖、captures 递增 | 1 | 首触也覆盖 |
| 落盘 | 无归因不写盘 | 1 | 空对象也落盘 |
| 落盘 | 读回重走清洗（本机被手工改脏） | 1 | 读回直接信 JSON |
| 落盘 | 畸形 JSON / 全非法快照按 null 处理不抛 | 1 | 去掉 try/catch |
| 落盘 | `clearAttribution` 后读回 null | 1 | 删 key 写错 |
| 诊断 | 输出行序与「last 只在与 first 不同时重复」 | 2 | 无条件重复输出 last |
| 诊断 | 无归因返回空数组 | 1 | 返回一行占位串 |
| 边界证人 | 不 import analytics、不调 `track`、不碰推荐码解析 | 2 | 加一行 `track('utm_seen', …)` |
| 边界证人 | 文件头「不上送 / 下界 / ALLOWED_PROPERTY_KEYS」三处锚点在位 | 3 | 删注释 |

## 7. 未做与残余（不算绿）

1. **发射点未接线**：App 的深链回调（`Linking` 的 url 事件）目前只走推荐码那条路径，
   没有调用 `captureAttributionFromUrl()`。所以现在**生产上不会有任何本机归因数据**，
   这与 KG-25/KG-31 反复声明的「屏已到位、流量仍零」是同一件事。接线属下一批：
   要在 `App.tsx` 的链接监听里加一次调用，并把 `attributionForDiagnostics()` 挂进导出面。
2. **删除路径未纳管**：账号删除/登出的本地清理清单里没有 `@closenai/attribution`
   （`PENDING_REFERRAL_KEY` 同样只在 referral 模块内部清理）。归因值已按 PII 规则清洗过，
   但「用户删除数据时本机这条键也要跟着清」是隐私承诺的一部分，属清理面任务，本批不顺手改。
3. **campaign link 的取值表不落地**：见 §4，投放前需以 ASC 后台当场生成的链接为准。
4. **无第二来源交叉核对**：本机没有任何生产数据可验证这套清洗规则在真流量下的漏检率。

## 8. 回滚

删 `frontend/src/services/attribution.ts` 与 `__tests__/attribution.test.ts` 即回到今天状态：
没有运行时接入点、没有契约变化、没有服务端列变化。

## 9. 实测计数与自证（2026-11-02）

跑法：`./.venv/bin/python scripts/mutate_kg19_kg20_gates.py`（串行、`copy2` 备份写回、`filecmp` 逐字节复核，全程不用 `git checkout` 恢复）。

| 项 | 实测 |
|---|---|
| `frontend/__tests__/attribution.test.ts` | **31 passed** |
| `backend/tests/test_keyword_funnel_doc.py`（KG-19，同批基线） | **8 passed** |
| `test_metrics_export.py` + `test_keyword_funnel_doc.py` 合跑 | **26 passed**（两张表共存不串台） |
| `attribution.test.ts` + `referral.test.ts` 合跑 | **64 passed / 2 suites** |
| `npx tsc --noEmit` | **exit=0** |
| 全量前端 jest | **1850 passed / 64 suites**（本批前 1819/63，差值＝新增 31 条 + 1 suite） |
| 全量后端 pytest | **1811 passed / 2 failed / 90 skipped**（那 2 条与本批无关，是 KG-28 批已登记的既有红灯：`test_canonical_schema.py` 的 canonical 引用、`test_r5_interop_broker.py` 要真库＋真 broker） |
| 变异 harness `--check` | 锚点全部命中一次 |
| 红闸（A 族 11 条打在 `attribution.ts`，K 族 9 条打在 `docs/metrics-dictionary.md`） | **20/20 打红** |
| 绿闸（GA1 加 12 行注释、GA2 放宽字符集到允许 `@` 以证 PII 层不是死代码、GK1 表上方加引用注释） | **3/3 保持绿** |

**带日期的判据勘误（A10 第一次失手）**：A10 把 `SAFE_PATH_RE` 的字符集放进点号，本意是让
`/R/../../etc` 这类穿越串被当路径存下，但第一次跑 **exit=0 没打红**——那条用例先撞上「首字符必须小写」
（`/R/` 是大写），放不放点号都红不了它，所以红的是我的用例、不是规则。根因**是用例缺区分度，不是代码有洞**。
修法：在 `frontend/__tests__/attribution.test.ts:191` 补一条小写开头、含 `..` 的
`expect(sanitizeLandingPath(`${BRAND}/en/../../etc/passwd`)).toBeNull();`——只有点号规则能判住它。
补完复跑 A10 打红 ✓，全量 **20/20**（见上表）。
