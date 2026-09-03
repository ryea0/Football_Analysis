#!/usr/bin/env bash
# 载体 A：system crontab 装配 / 卸载 fa 三条定时 job（spec §9.6，双载体之一）
#
# 用法：cron_install.sh            装配（幂等：标记块整块替换，其余条目不动）
#       cron_install.sh --remove   卸载（只删标记块）
#
# 时刻事实单在 cron_jobs.txt（本脚本与 hermes_cron_install.sh 同读一份）。
# 与 hermes 载体互斥：切换 = 本脚本 --remove 后装另一载体（双跑会重复触发）。
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
WRAPPER="$SCRIPT_DIR/fa_cron.sh"
JOBS_FILE="$SCRIPT_DIR/cron_jobs.txt"
BEGIN="# BEGIN fa-cron-m5"
END="# END fa-cron-m5"

# 标记块用整行精确匹配（awk），不用 sed 正则——标记串里的字符不构成正则元语义
strip_block() {
  crontab -l 2>/dev/null | awk -v b="$BEGIN" -v e="$END" '
    $0 == e { skip = 0; next }
    $0 == b { skip = 1; next }
    !skip { print }
  ' || true
}

if [[ "${1:-}" == "--remove" ]]; then
  strip_block | crontab -
  echo "已从 crontab 移除 fa 三条 job"
  exit 0
fi

[[ -x "$WRAPPER" ]] || { echo "wrapper 不可执行：$WRAPPER（chmod +x）" >&2; exit 1; }
[[ -r "$JOBS_FILE" ]] || { echo "读不到事实单：$JOBS_FILE" >&2; exit 1; }
mkdir -p "$SCRIPT_DIR/../logs/cron"   # cron 行的重定向目标须先在，wrapper 才跑得起来

block="$BEGIN"
while IFS=$'\t' read -r job schedule arg; do
  [[ "$job" =~ ^# || -z "$job" ]] && continue
  block+=$'\n'"$schedule $WRAPPER $arg >> $SCRIPT_DIR/../logs/cron/wrapper.log 2>&1"
done < "$JOBS_FILE"
block+=$'\n'"$END"

{ strip_block; printf '%s\n' "$block"; } | crontab -

echo "crontab 载体已装配（时刻=北京时间，机器时区 Asia/Shanghai）："
crontab -l | awk -v b="$BEGIN" -v e="$END" '$0==b{p=1} p{print} $0==e{p=0}'
