# 开发入口（T0-01a）。编排事实都在 deploy/compose/ 里，这里只给一条命令的面。
COMPOSE_DEV = docker compose -f deploy/compose/docker-compose.dev.yml --env-file deploy/compose/.env

.PHONY: dev-up dev-down dev-logs dev-ps

dev-up: ## 起本地全套（pg+rabbitmq+migrate job+py-api+ai-rpc+worker+go-api）
	@test -f deploy/compose/.env || (echo "先 cp deploy/compose/.env.example deploy/compose/.env 并填 JWT_SECRET/CRON_SECRET（见 deploy/README.md）" && exit 1)
	$(COMPOSE_DEV) up -d --build

dev-down: ## 停并保留数据卷
	$(COMPOSE_DEV) down

dev-logs: ## 跟日志（Ctrl-C 退出）
	$(COMPOSE_DEV) logs -f

dev-ps: ## 端口发布面自查（验收：5432/5672/50051/8080 不对外）
	$(COMPOSE_DEV) ps
