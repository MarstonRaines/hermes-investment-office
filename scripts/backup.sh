#!/usr/bin/env bash
# ============================================================
# Hermes Investment Office —— 备份脚本（冻结规范 §41）
#   - PostgreSQL: daily pg_dump（经 docker exec，容器内执行）
#   - 文件: data/ 目录增量备份（rsync 快照）
# 用法:
#   ./scripts/backup.sh              # 完整备份到 BACKUP_ROOT
#   ./scripts/backup.sh --restore-db <file>   # 恢复 PG dump（谨慎）
#   cron 示例: 0 3 * * * /path/scripts/backup.sh >> /path/logs/backup.log 2>&1
# ============================================================
set -euo pipefail

BACKUP_ROOT="${BACKUP_ROOT:-${HOME}/hermes-backups}"
DATE_STAMP="$(date +%Y%m%d_%H%M%S)"
DB_CONTAINER="hermes-db"
PG_USER="hermes"
PG_DB="hermes"
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATA_DIR="${PROJECT_ROOT}/data"
RETENTION_DAYS="${RETENTION_DAYS:-30}"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

mkdir -p "${BACKUP_ROOT}/pg" "${BACKUP_ROOT}/data"

# ---------- 1. PostgreSQL dump（经容器内部网络） ----------
backup_db() {
  local dump_file="${BACKUP_ROOT}/pg/hermes_${DATE_STAMP}.dump"
  log "开始 PG dump: ${dump_file}"
  if ! docker exec "${DB_CONTAINER}" pg_dump -U "${PG_USER}" -d "${PG_DB}" \
      --format=custom --no-owner --no-privileges > "${dump_file}"; then
    log "ERROR: PG dump 失败"
    rm -f "${dump_file}"
    return 1
  fi
  log "PG dump 完成: $(du -h "${dump_file}" | cut -f1)"
}

# ---------- 2. data/ 目录增量备份（rsync） ----------
backup_data() {
  local snap_dir="${BACKUP_ROOT}/data/current"
  local stamp_dir="${BACKUP_ROOT}/data/${DATE_STAMP}"
  log "开始 data/ 增量备份（rsync hardlink 快照）"
  if [[ -d "${snap_dir}" ]]; then
    cp -al "${snap_dir}" "${stamp_dir}"    # hardlink 快照（零拷贝历史）
  fi
  rsync -a --delete "${DATA_DIR}/" "${snap_dir}/"
  # 一致性：stamp 指向与 current 相同的 inode
  if [[ ! -d "${stamp_dir}" ]]; then
    cp -al "${snap_dir}" "${stamp_dir}"
  fi
  log "data/ 快照完成: ${stamp_dir}"
}

# ---------- 3. 保留期清理 ----------
cleanup() {
  log "清理超过 ${RETENTION_DAYS} 天的备份"
  find "${BACKUP_ROOT}/pg" -name "hermes_*.dump" -mtime "+${RETENTION_DAYS}" -delete
  find "${BACKUP_ROOT}/data" -maxdepth 1 -type d -name "20*" -mtime "+${RETENTION_DAYS}" -delete
}

# ---------- 恢复（手动谨慎操作） ----------
restore_db() {
  local dump_file="$1"
  [[ -f "${dump_file}" ]] || { echo "dump 文件不存在: ${dump_file}"; exit 1; }
  echo "将恢复到 ${PG_DB}（容器 ${DB_CONTAINER}）—— 现有数据将被覆盖！"
  read -r -p "确认恢复? [y/N] " ans
  [[ "${ans}" == "y" ]] || { echo "取消"; exit 0; }
  docker exec -i "${DB_CONTAINER}" pg_restore -U "${PG_USER}" -d "${PG_DB}" \
      --clean --if-exists --no-owner < "${dump_file}"
  echo "恢复完成"
}

case "${1:-}" in
  --restore-db) restore_db "${2:?需要 dump 文件路径}" ;;
  *) backup_db && backup_data && cleanup && log "备份全部完成" ;;
esac
