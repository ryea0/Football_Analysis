#!/usr/bin/env bash
# 载体 B：hermes cron 装配 / 卸载 fa 三条定时 job（spec §9.6，双载体之一）
#
# 用法：hermes_cron_install.sh            装配（幂等：先按名移除旧 job 再建）
#       hermes_cron_install.sh --remove   卸载
#
# 形态：--no-agent --script——hermes 只当**纯调度器**（无 LLM 消耗）；wrapper
# 成功时 stdout 静默=不送，失败告警走 fa 自己的 `fa ops alert` 推送路径，
# gateway 因此不需要配 TG 代理。--script 只收 ~/.hermes/scripts/ 下的路径，
# 故每个 job 生成一个启动器指回项目 wrapper。
#
# 前提：hermes gateway 已安装且在跑（`hermes gateway install` 装用户服务，或前台
# `hermes gateway`），否则 job 建了也不会触发（`hermes cron status` 可查）。
# 与 crontab 载体互斥：切换 = 本脚本 --remove 后装 cron_install.sh（双跑会重复触发）。
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
WRAPPER="$SCRIPT_DIR/fa_cron.sh"
JOBS_FILE="$SCRIPT_DIR/cron_jobs.txt"
HERMES_SCRIPTS="$HOME/.hermes/scripts"
NAMES=()

[[ -x "$WRAPPER" ]] || { echo "wrapper 不可执行：$WRAPPER（chmod +x）" >&2; exit 1; }
[[ -r "$JOBS_FILE" ]] || { echo "读不到事实单：$JOBS_FILE" >&2; exit 1; }

while IFS=$'\t' read -r job schedule arg; do
  [[ "$job" =~ ^# || -z "$job" ]] && continue
  NAMES+=("fa-$job")
done < "$JOBS_FILE"

if [[ "${1:-}" == "--remove" ]]; then
  for name in "${NAMES[@]}"; do
    hermes cron remove "$name" || true   # job_id 直收名字；不在库里的报错忽略
  done
  echo "已从 hermes cron 移除：${NAMES[*]}"
  exit 0
fi

mkdir -p "$HERMES_SCRIPTS"
while IFS=$'\t' read -r job schedule arg; do
  [[ "$job" =~ ^# || -z "$job" ]] && continue
  hermes cron remove "fa-$job" >/dev/null 2>&1 || true   # 幂等：先按名移除旧 job
  launcher="$HERMES_SCRIPTS/fa_cron_$job.sh"
  cat > "$launcher" <<EOF
#!/usr/bin/env bash
# 由 hermes_cron_install.sh 生成——指回项目 wrapper（job 事实单在项目 scripts/）
exec "$WRAPPER" "$arg"
EOF
  chmod +x "$launcher"
  hermes cron create "$schedule" --script "$launcher" --no-agent --name "fa-$job"
done < "$JOBS_FILE"

echo "hermes cron 载体已装配（时刻=北京时间）。gateway 状态："
hermes cron status || true
