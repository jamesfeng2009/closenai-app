# deploy — 编排与调度

## 本地开发编排（T0-01a，2026-10-18）

一条入口（仓库根）：

```sh
cp deploy/compose/.env.example deploy/compose/.env   # 填 JWT_SECRET/CRON_SECRET；权限 600
make dev-up    # = docker compose -f deploy/compose/docker-compose.dev.yml up -d --build
make dev-ps    # 端口面自查；make dev-down / make dev-logs
```

- 服务：`postgres:16`、`rabbitmq:3-management`、`migrate`（一次性 `alembic upgrade head`，
  schema 唯一事实源＝alembic；`go-core/db/schema` 双写风险归方案 §17）、`py-api`、`ai-rpc`、
  `py-ai-worker`、`go-api`——这是仓库里第一次把 5 进程＋1 broker 编在同一张网上。
- 端口只发布 `127.0.0.1:8000`（py-api）与 `127.0.0.1:15672`（RabbitMQ UI）；
  `go-api` 无宿主端口，只在网络内 `go-api:8000` 可达。5432/5672/50051 一律不对外。
- 依赖顺序用 `service_healthy`（PG/RabbitMQ healthcheck）＋ `service_completed_successfully`
  （migrate job）。
- 本机拉不到 docker.io（2026-10-13 实测 Bad Gateway）：在 `.env` 里用
  `*_IMAGE=docker.m.daocloud.io/library/...` 覆盖，上游默认名保持可移植。
- 生产模板 `docker-compose.prod.yml`＋`Caddyfile` 是代码面先行：密钥一律 `${VAR:?}`、
  灰度/开关键默认 0（编排就位≠流量放行）；整条 T0-01b 验收仍绑 VPS 上线。

## 调度源（T0-02，2026-10-18 起）

现役**唯一**调度声明是 [`schedule/crontab`](schedule/crontab)（15 条：7 条整点每小时、
4 条按日、2 条按周、2 条 `*/5`——`/cron/health` 保热与 T1-01 的 `/cron/export-jobs` 导出队列；按日那条 `0 4 * * *` 是 T1-04 的付费交付审计）。旧源 `vercel.json` 同日退役（B-02 判默认删除）；
`backend/tests/test_cron_schedule_coverage.py` 逐行解析 crontab 对账 `/api/v1/cron/*`
路由，双向判据：端点没被排程 ⇒ 红；排程敲不存在的路径 ⇒ 红；docstring 宣称的节律
（每小时/每日/每周/每 5 分钟）与表达式不同形 ⇒ 红。

三条 POST（`process-user` / `follow-up` / `purge-user`）不在 crontab 里：它们是
QStash 公网回调（验签 `Upstash-Signature`），调度由投递侧注册，见
`backend/services/cron_service.py`。

## 安装（VPS 宿主）

```sh
# 1. 密钥：只放宿主，权限 600，不入库（CRON_SECRET 须与后端 env 一致）
sudo mkdir -p /etc/closenai
sudo sh -c 'umask 077; printf "CRON_SECRET=%s\n" "<从密码管理器取>" > /etc/closenai/cron.env'
sudo chmod 600 /etc/closenai/cron.env

# 2. 装排程（会覆盖该用户的 crontab——先 crontab -l 导出留档）
crontab deploy/schedule/crontab
```

## 时区

服务器 crontab 按**宿主时钟**执行，本仓所有表达式按 **UTC** 书写（`date -u` 核对
`TZ=` 未在该 crontab 上方声明 ⇒ 取系统时区；若宿主非 UTC，要么改系统时区，
要么整文件换算后重贴，不许混用）。业务时区（用户本地 22:00 窗口、周日晚等）在
`cron_service` 内按用户行上的 timezone 计算，与调度器时区无关。

## 密钥口径

- 鉴权走 `Authorization: Bearer $CRON_SECRET`（与 `verify_cron_secret` 的读取面一致）；
  这是任务板原写 `X-Cron-Secret` 的**有意偏离**——代码面读的是 Bearer 头，改 crontab
  一行不动安全代码，反向则要动 `verify_cron_secret` 与其测试。结案回执已登记。
- `/cron/health` 本身不校验密钥（无副作用保热端点），crontab 仍统一带头。
- crontab 文件里**永不**出现明文密钥；`$CRON_SECRET` 由每行
  `. /etc/closenai/cron.env` 现场加载。

### k8s 侧 Secret `closenai-runtime` 的键面（T3-03，2026-10-19）

`deploy/k8s/{30-go-api,40-py-api,50-ai-rpc}.yaml` 都是 `envFrom: secretRef`，所以
**加一个键就两侧同时可见**，不需要改任何 Deployment。代价是这张键面没有清单侧的
登记表——`10-config.yaml` 头部那句「创建命令见本目录 README」指的 `deploy/k8s/README.md`
**并不存在**（登记于裁决七-10-14 之外的同一处文档悬空引用），所以键面在此逐条列出：

```bash
# 值一律现场给，不写进本文件、不入库（同「凭据不入库」口径）。
kubectl -n closenai create secret generic closenai-runtime \
  --from-literal=DATABASE_URL=...      --from-literal=JWT_SECRET=... \
  --from-literal=CRON_SECRET=...       --from-literal=RESEND_API_KEY=... \
  --from-literal=APP_STORE_SHARED_SECRET=... \
  --from-literal=GO_SERVICE_TOKEN="$(openssl rand -hex 32)"   # T3-03 新增
```

- `GO_SERVICE_TOKEN` 是**跨语言共享**键：go-api 用它决定 `/internal/v3/*` 注册与否
  （空 ⇒ 整面不注册，回 404 信封），py-api 用它作服务间凭据（空 ⇒ 三条洞察端点在
  **拨号之前**回 503）。两侧不同值＝每一次拨号 403，而它长得像 Go 挂了——
  所以轮换时两条 Deployment 要同批滚动，`kubectl -n closenai rollout restart deploy/go-api deploy/py-api`。
- 其余非共享项（`POSTHOG_API_KEY`／provider keys／`APNS_*`）按各自工单口径进同一张 Secret。

## 日志与排障

- 行尾 `>/dev/null` 只丢 stdout；stderr 与 curl 非零退出仍进 cron 邮件/`/var/log/syslog`
  （发行版不同位置不同，宿主上先 `crontab -l`＋一次手动重放确认落点）。
- 手动重放（排障/补跑，与 crontab 逐字同形）：

```sh
. /etc/closenai/cron.env && curl -fsS -H "Authorization: Bearer $CRON_SECRET" \
  http://127.0.0.1:8000/api/v1/cron/journey
```

- 后端响应是五键信封（`code/message/data/trace_id/user_id`），排查用响应里的
  `trace_id` 串日志，不在 cron 侧猜。

### 异步指标与告警口径（T2-05，2026-10-19）

出处：`go-core/internal/obs/queue_metrics.go`（形状）、`go-core/internal/messaging/dlq`（取数）、
`go-core/cmd/go-api/main.go`（起不起）。判据：`queue_metrics_test.go` 8 条＋`cmd/go-api` 7 条。

- 三个 `msg` 值逐字：`queue_metrics`（每轮一条汇总）、`queue_metric`（**只给有积压的**队列，
  一队列一行）、`queue_metrics_read_failed`（这一轮没读到数）。指标名：
  `queue_depth`、`oldest_message_age_seconds`、`dlq_total`，另有 `queues_scanned`、
  `oldest_age_known`、`oldest_age_queue`、`kind`、`consumers`。
- 周期 60s 是**包内常量**，没有 env 键；开不起不起由出箱与入站两个既有 kill switch 决定
  （任一开着才起），地址复用 `CLOSEN_AMQP_URL`。两个都关（＝现网默认）时进程里这句话是
  证人：`queue metrics 未启动：出箱与入站两个 kill switch 都关着`。
- **两条告警**（Loki 侧，日志驱动；k3s 今天没有 Prometheus operator，所以没有 recording rule，
  规则就写成 LogQL 查询、由 Loki ruler 或人工看板执行——**这两条今天还没有执行者，见末尾**）：

```logql
# ① 积压消息老化：任一轮汇总里「最老那条」> 10 分钟
max_over_time({job="closenai", app="go-api"} | json | msg="queue_metrics"
  | oldest_age_known="true" | unwrap oldest_message_age_seconds [5m]) > 600

# ② DLQ 在长：终态面总数一天涨过 10 条
increase({job="closenai", app="go-api"} | json | msg="queue_metrics" | unwrap dlq_total [24h]) > 10
```

  ①必须带 `oldest_age_known="true"`：读不到年龄时那一格是 `oldest_age_known=false` 而**不是 0**，
  漏了这个过滤，「broker 挂了读不到」会被告警读成「一切健康」（这正是巡检面拒绝假健康的那条口径）。
- **年龄的口径只有一条，别按直觉读**：死信进终态面时 Python 的
  `backend/ai/mq/publisher.py:131`（`AioDeadLetterPublisher.park`）会**重写 timestamp**，
  所以 `oldest_message_age_seconds` 量的是「进终态面多久」，不是「第一次失败多久」。
  要判后者看 `--list` 输出里的 `x_death[].time`。这条口径由 2026-10-19 真 broker 集成测跑实。
- 处置入口：`go run ./tools/dlqreplay -list [-queue dlq.<name>]`（只读）、
  `-replay -queue dlq.<name> -limit N`（只允许终态面）、`-purge -queue dlq.<name> -confirm <逐字同名>`。
  它需要 `CLOSEN_AMQP_URL`，没设时工具直接退错、不产读数。
- ⚠️ 诚实登记：`60-observability/` 的 loki＋promtail 清单**尚未 apply 到生产 k3s**
  （上刀＝T-9，另行点名授权）。所以这两条告警今天**没有执行者**，按口径记「未跑」，
  不算绿——本批交付的是日志面与规则文本，不是已在响的告警。

## 回滚

恢复上一版 crontab 文件后 `crontab deploy/schedule/crontab` 重装即可；宿主上的
当前生效版本务必先 `crontab -l > <留档路径>` 再改——该状态不入库，git 帮不了你。
