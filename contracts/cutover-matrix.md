# 切流 path × method 矩阵（T6-02）

> 本文件由 `go run ./tools/cutovermatrix -write` 生成，**禁止手改**。
> 判据住在 `go-core/internal/cutover/matrix.go::Violations()`，闸测在
> `go-core/internal/cutover/path_method_matrix_test.go`；三张真源分别是
> `deploy/k8s/50-ingress.yaml`（反代归属）、`contracts/endpoints.md`（path 上有几种方法）、`internal/httpapi` 的路由声明表（Go 答得了哪些）。
> 
> ## 为什么这张表存在
>
> Ingress（与 Caddy/Nginx 同理）的 **path 匹配不看 HTTP 方法**（决策点 D-10）。
> 于是「这个域的某条 path 我 Go 已经实现了 GET」≠「这条 path 可以切」：
> 同一条 path 上还挂着 POST/DELETE，一刀下去它们**一起**搬到 go-api，
> 而 Go 那台 mux 没注册它们 ⇒ 405——与灰度百分比无关，0% 也救不回来
> （转交发生在 handler 内部，路径没匹配上根本进不到 handler）。
>
> **硬性结论（不许「顺手试一下」）**：`memories` 与 `reflections` 两个域，
> 在 Go 补齐写方法（`POST /api/v1/reflections`、`POST /api/v1/memories`、
> `DELETE /api/v1/memories/{memory_id}`，含同 path 的 search/version 那几条）之前，
> **不得**在反代清单里加指向 go-api 的 path；`mood` 域只按端点列 `Exact`，
> 不许写成 `Prefix /api/v1/mood`（D-5：键是域级、刀是端点级）。

## 矩阵

| 域键（放量变量） | 默认百分比 | 反代已上刀 | Go 已注册（方法 路径） | 方法齐全的 path | 缺方法的 path（上刀前必须补） | 是否可切 | 原因 |
|---|---|---|---|---|---|---|---|
| `CLOSEN_CUTOVER_ANALYTICS` | 0% | Prefix /api/v1/analytics | POST /api/v1/analytics/batch | /api/v1/analytics/batch | — | **可切** | 已上刀 1 条规则，命中的契约操作全部由 Go 回答（Prefix /api/v1/analytics） |
| `CLOSEN_CUTOVER_CURRICULUM` | 0% | Prefix /api/v1/curriculum | GET /api/v1/curriculum/week、POST /api/v1/curriculum/{event_id}/read | /api/v1/curriculum/week、/api/v1/curriculum/{}/read | — | **可切** | 已上刀 1 条规则，命中的契约操作全部由 Go 回答（Prefix /api/v1/curriculum） |
| `CLOSEN_CUTOVER_DISCOVERY` | 0% | Prefix /api/v1/discovery | GET /api/v1/discovery/daily | /api/v1/discovery/daily | — | **可切** | 已上刀 1 条规则，命中的契约操作全部由 Go 回答（Prefix /api/v1/discovery） |
| `CLOSEN_CUTOVER_JOURNEY` | 0% | Prefix /api/v1/journey | GET /api/v1/journey/events、POST /api/v1/journey/events/acknowledge | /api/v1/journey/events、/api/v1/journey/events/acknowledge | — | **可切** | 已上刀 1 条规则，命中的契约操作全部由 Go 回答（Prefix /api/v1/journey） |
| `CLOSEN_CUTOVER_MEMORIES` | 0% | —（未上刀） | GET /api/v1/memories、GET /api/v1/memories/{memory_id} | — | /api/v1/memories、/api/v1/memories/search、/api/v1/memories/version、/api/v1/memories/{} | **不可切** | 未上刀：path 匹配不看方法，而这个域还有方法不全的 path（/api/v1/memories、/api/v1/memories/search、/api/v1/memories/version、/api/v1/memories/{}）⇒ Go 补齐写方法之前不许加 path（D-10） |
| `CLOSEN_CUTOVER_MOOD` | 0% | Exact /api/v1/mood/history、Exact /api/v1/mood/trend | GET /api/v1/mood/history、GET /api/v1/mood/trend | /api/v1/mood/history、/api/v1/mood/trend | /api/v1/mood/checkin | **可切** | 已上刀 2 条规则，命中的契约操作全部由 Go 回答（Exact /api/v1/mood/history、Exact /api/v1/mood/trend） |
| `CLOSEN_CUTOVER_ONBOARDING` | 0% | Prefix /api/v1/onboarding | POST /api/v1/onboarding/first-read、GET /api/v1/onboarding/first-read | /api/v1/onboarding/first-read | — | **可切** | 已上刀 1 条规则，命中的契约操作全部由 Go 回答（Prefix /api/v1/onboarding） |
| `CLOSEN_CUTOVER_REFLECTIONS` | 0% | —（未上刀） | GET /api/v1/reflections | — | /api/v1/reflections、/api/v1/reflections/generate | **不可切** | 未上刀：path 匹配不看方法，而这个域还有方法不全的 path（/api/v1/reflections、/api/v1/reflections/generate）⇒ Go 补齐写方法之前不许加 path（D-10） |

## 口径备忘

- **域级键 × 端点级 path** 是合法的（D-5），但一条 `Prefix` 只许用在「方法齐全的域」上；
- 「不可切」不是缺陷而是**裁决**：它拦的是「把写方法搬进没注册它的进程」这件事；
- 百分比为 0 时 go-api 把请求**逐字转交** py-api（`internal/cutover`），
  所以「已上刀」不等于「已放量」——但「上刀且方法不全」在 0% 也已经是坏的：
  没匹配上的路径进不到 handler，转交无从谈起。

反代第二处出口（`deploy/compose/Caddyfile`）解析到的归属：go-api 不对公网暴露，故该清单里不应出现任何指向 go-api 的 path。
