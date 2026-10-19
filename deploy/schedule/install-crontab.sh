#!/bin/sh
# T6-04（2026-10-20）· 调度源的安装入口。
#
# 这个脚本只做一件事，而且是有价值的那一件：**拒绝把 crontab 装到非生产那台机器上**。
# 为什么不能只写在 README 里——`crontab deploy/schedule/crontab` 是一行可以直接复制的命令，
# 复制的人不会先问「这台是 staging 还是生产」，而注释不会跳出来反对（本仓口径：闸拦人）。
# 装错的后果不是报错，是**双跑**：staging 那台也会按小时打 /cron/daily-message，
# 而两个进程各自连着自己的库、看着同一份调度声明。
#
# 判据从哪来：宿主上 `/etc/closenai/env` 里那一行 `ENVIRONMENT=`（与容器读的是同一个词表，
# 见 backend/core/env_isolation.py 与 go-core/internal/config/config.go）。
# 文件不存在／那行不存在／值不在词表里／值是 staging 或 development ⇒ 一律拒绝并非零退出。
# 只认 production 与它的既有别名 prod。
#
# 用法：
#   deploy/schedule/install-crontab.sh               # 装（会覆盖该用户现有 crontab）
#   deploy/schedule/install-crontab.sh --check       # 只判不装（排障与闸门用这条）
#   deploy/schedule/install-crontab.sh --env-file f  # 换判据来源（测试用，生产别用）
#   deploy/schedule/install-crontab.sh --crontab-file f  # 换调度源（同上，只用于判据自证）
#
# 现存的 crontab 请先自行留档：crontab -l > <留档路径>（本脚本不替你备份，
# 也不会在拒绝分支上动任何状态——它宁可什么也不做）。
set -eu

CRONTAB_FILE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)/crontab
ENV_FILE=${CLOSEN_CRON_ENV_FILE:-/etc/closenai/env}
CHECK_ONLY=0

die() {
  echo "install-crontab: 拒绝安装：$1" >&2
  exit 1
}

# 下面所有消息里，紧跟中文标点的变量一律写成 ${NAME}：
# shell 是按字节判断「合法变量名字符」的，locale=C 时全角括号的首字节 0xef 会被并进名字，
# 于是 set -u 下报 `PORTS\xef: unbound variable`——判据没错，但脚本在被检出的那一刻自己先崩，
# 运维看到的是一条读不懂的退出码而不是这条理由（2026-10-20 由 test_crontab_install_target.py 抓到）。

need_value() {
  # `$2` 在 set -u 下缺值会裸退出并打印 shell 的内部报错；这里换成一条能读的判断。
  [ $# -ge 2 ] && [ -n "${2:-}" ] || die "$1 需要一个参数值"
}

while [ $# -gt 0 ]; do
  case "$1" in
    --check) CHECK_ONLY=1 ;;
    --env-file) need_value "$@"; ENV_FILE=$2; shift ;;
    --env-file=*) ENV_FILE=${1#*=} ;;
    --crontab-file) need_value "$@"; CRONTAB_FILE=$2; shift ;;
    --crontab-file=*) CRONTAB_FILE=${1#*=} ;;
    *) echo "install-crontab: 未知参数 $1（只认 --check / --env-file / --crontab-file）" >&2; exit 2 ;;
  esac
  shift
done

[ -f "$ENV_FILE" ] || die "取不到 ${ENV_FILE}：这一台没有声明自己是哪一档，不许装调度"
[ -f "$CRONTAB_FILE" ] || die "取不到 ${CRONTAB_FILE}（调度源本体不在，装了也是空表）"

ENVIRONMENT=$(sed -n 's/^[[:space:]]*ENVIRONMENT[[:space:]]*=[[:space:]]*//p' "$ENV_FILE" | tail -n 1)
[ -n "$ENVIRONMENT" ] || die "$ENV_FILE 里没有 ENVIRONMENT= 那一行"

# 词表与两侧运行时同一张（D-T604-2）；这里逐字比，不做大小写折叠。
case "$ENVIRONMENT" in
  production|prod) ;;
  development|staging)
    die "ENVIRONMENT=$ENVIRONMENT 不是生产档：crontab 只装生产那台（工单 §怎么做.3）。
     staging 要跑同一批任务请显式手工重放，别让它与生产同时按小时打同一组端点" ;;
  *) die "ENVIRONMENT=$ENVIRONMENT 不在词表里（只认 development|staging|production|prod）" ;;
esac

# 调度源自己也要声明它属哪一台：这行标记与 README、与 backend/tests/
# test_crontab_install_target.py 三方对账（标记没了＝有人在改 crontab 时把口径删了）。
grep -q '^# INSTALL-TARGET: production-only$' "$CRONTAB_FILE" \
  || die "$CRONTAB_FILE 顶部的 INSTALL-TARGET: production-only 标记不在了"

# 每一行都只打回环上的生产端口：staging 那套 bind 的是 127.0.0.1:18000
# （见 docker-compose.staging.yml），所以这张表物理上够不到它。
# `|| true` 是必须的：grep 无匹配返回 1，而 `PORTS=$(...)` 这条赋值在 set -e 下会把
# 整个脚本就地带走——那时「调度源被掏空」这条判据根本来不及说话，只留一个裸退出码。
# 先剔掉注释行：判的是「这张表会打到哪些地址」，注释不参与执行。
# 不剔的话，一句「别打到 127.0.0.1:18000」的说明就会让安装拒启——那是文本 diff，不是闸。
PORTS=$(grep -v '^[[:space:]]*#' "$CRONTAB_FILE" | grep -Eo '127\.0\.0\.1:[0-9]+' || true)
PORTS=$(printf '%s\n' "$PORTS" | sort -u)
[ -n "$PORTS" ] || die "$CRONTAB_FILE 里一个回环目标都没有：调度源被掏空了"
# `grep -qv` 无匹配时返回 1，写成 `A && die` 会蹭 set -e 的边界（这里用 if 明确避开）：
# 「一行越界端口都没有」是**通过**分支，不能让它长得像失败。
if printf '%s\n' "$PORTS" | grep -qv '^127\.0\.0\.1:8000$'; then
  die "crontab 里出现了非 127.0.0.1:8000 的目标端口（端口面漂了：${PORTS}）"
fi

if [ "$CHECK_ONLY" = 1 ]; then
  echo "install-crontab: 判据通过（ENVIRONMENT=${ENVIRONMENT}，目标 ${CRONTAB_FILE}）——--check 不安装"
  exit 0
fi

echo "install-crontab: 即将用 $CRONTAB_FILE 覆盖当前用户的 crontab（先 crontab -l 留档）"
crontab "$CRONTAB_FILE"
echo "install-crontab: 已安装（ENVIRONMENT=${ENVIRONMENT}）"
