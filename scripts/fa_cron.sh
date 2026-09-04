#!/usr/bin/env bash
# fa 定时 job 唯一入口（system crontab 与 hermes cron 双载体共用，spec §9.6）
#
# 用法：fa_cron.sh <daily|am|pm|weekly|evolve>
#
# 职责（与载体无关）：
#   1. 环境补齐：PATH 补 ~/.local/bin（uv 所在，cron 默认 PATH 没有）；
#      导出 clash 代理（TG 推送区域封锁的前提，CLAUDE.md「TG 推送代理依赖」——
#      代理离线时管线按设计降级：runs.summary 记原因、run 不中断）
#   2. 跑 job，输出全量落 logs/cron/<日期>_<job>.log（成功时 stdout 静默——
#      hermes --no-agent 模式下空 stdout=不送，双载体都不重复推送）
#   3. 失败（exit≠0）→ 立即 `fa ops alert` TG 告警（spec 风险 #6「跑批失败要有
#      TG 告警」）；告警自身失败不吞 job 退出码（|| true 兜底）
#   4. daily 成功后跑 `fa ops watchdog`（spec 风险 #6「daily 失败超 1 天即告警」：
#      最近两次成功 daily 间隔 >25h = 漏跑/连续失败）
set -u

JOB="${1:-}"
if [[ "$JOB" != daily && "$JOB" != am && "$JOB" != pm && "$JOB" != weekly && "$JOB" != evolve ]]; then
  echo "用法: $0 <daily|am|pm|weekly|evolve>" >&2
  exit 64
fi

PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
export PATH="$HOME/.local/bin:$PATH"
export https_proxy="${https_proxy:-http://127.0.0.1:7890}"
export http_proxy="${http_proxy:-http://127.0.0.1:7890}"

LOG_DIR="$PROJECT_ROOT/logs/cron"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/$(date +%F)_$JOB.log"

case "$JOB" in
  daily)  CMD=(fa run daily) ;;
  am)     CMD=(fa run matchday --phase am) ;;
  pm)     CMD=(fa run matchday --phase pm) ;;
  weekly) CMD=(fa ops weekly) ;;   # 周一 07:00：daily 结算后的上周小结（空周静默）
  # M6（§12.7）：C 线周检——窗口收口才触发反思，否则一行日志空转退出 0。
  # 周日 03:17（事实单 scripts/cron_jobs.txt）：避开 daily/am/pm/weekly。
  # 失败告警沿用本 wrapper 的 fa ops alert（「静默停摆」=不触碰 A/B 线，
  # 不指对运维静默——设计档 R7）
  evolve)  CMD=(fa evolve tick) ;;
esac

cd "$PROJECT_ROOT"
{
  echo "=== $(date -Is) job=$JOB begin ==="
  uv run "${CMD[@]}"
  rc=$?
  echo "=== $(date -Is) job=$JOB end rc=$rc ==="
} >> "$LOG" 2>&1

if (( rc != 0 )); then
  uv run fa ops alert \
    "❌ fa cron job 失败：$JOB rc=$rc（$(date '+%F %T')）——$(tail -n 3 "$LOG" | tr '\n' ' ')" \
    >> "$LOG" 2>&1 || true
  exit "$rc"
fi

if [[ "$JOB" == daily ]]; then
  uv run fa ops watchdog >> "$LOG" 2>&1 || true
fi

exit 0
