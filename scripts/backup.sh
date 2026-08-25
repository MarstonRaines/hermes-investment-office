#!/usr/bin/env bash
# Hermes Investment Office：每日备份，保留 30 份每日备份与 12 份周备份。
set -euo pipefail

HERMES_BACKUP_ROOT="${HERMES_BACKUP_ROOT:-${HOME}/hermes-backups}"
HERMES_BACKUP_STAMP="$(date +%Y%m%d_%H%M%S)"
HERMES_DB_CONTAINER="hermes-db"
HERMES_BACKEND_CONTAINER="hermes-backend"
HERMES_PG_USER="${HERMES_POSTGRES_USER:-hermes}"
HERMES_PG_DB="${HERMES_POSTGRES_DB:-hermes}"
HERMES_DAILY_KEEP="${HERMES_DAILY_KEEP:-30}"
HERMES_WEEKLY_KEEP="${HERMES_WEEKLY_KEEP:-12}"
HERMES_LAST_DB_DUMP=""
HERMES_LAST_DATA_ARCHIVE=""

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

mkdir -p \
  "${HERMES_BACKUP_ROOT}/daily/pg" \
  "${HERMES_BACKUP_ROOT}/daily/data" \
  "${HERMES_BACKUP_ROOT}/weekly/pg" \
  "${HERMES_BACKUP_ROOT}/weekly/data"

backup_db() {
  HERMES_LAST_DB_DUMP="${HERMES_BACKUP_ROOT}/daily/pg/hermes_${HERMES_BACKUP_STAMP}.dump"
  log "开始 PostgreSQL 备份"
  if ! docker exec "${HERMES_DB_CONTAINER}" \
      pg_dump -U "${HERMES_PG_USER}" -d "${HERMES_PG_DB}" \
      --format=custom --no-owner --no-privileges > "${HERMES_LAST_DB_DUMP}"; then
    rm -f -- "${HERMES_LAST_DB_DUMP}"
    return 1
  fi
  log "PostgreSQL 备份完成：$(du -h "${HERMES_LAST_DB_DUMP}" | cut -f1)"
}

backup_data() {
  HERMES_LAST_DATA_ARCHIVE="${HERMES_BACKUP_ROOT}/daily/data/hermes_data_${HERMES_BACKUP_STAMP}.tar.gz"
  log "开始数据卷备份"
  if ! docker exec "${HERMES_BACKEND_CONTAINER}" \
      tar -C /var/lib/hermes/data -czf - . > "${HERMES_LAST_DATA_ARCHIVE}"; then
    rm -f -- "${HERMES_LAST_DATA_ARCHIVE}"
    return 1
  fi
  log "数据卷备份完成：$(du -h "${HERMES_LAST_DATA_ARCHIVE}" | cut -f1)"
}

create_weekly_copy() {
  if [[ "$(date +%u)" == "6" ]]; then
    cp -p -- "${HERMES_LAST_DB_DUMP}" "${HERMES_BACKUP_ROOT}/weekly/pg/"
    cp -p -- "${HERMES_LAST_DATA_ARCHIVE}" "${HERMES_BACKUP_ROOT}/weekly/data/"
    log "已生成本周备份"
  fi
}

prune_prefix() {
  local target_dir="$1"
  local prefix="$2"
  local keep="$3"
  local files
  shopt -s nullglob
  files=("${target_dir}"/"${prefix}"_*)
  while (( ${#files[@]} > keep )); do
    rm -f -- "${files[0]}"
    files=("${target_dir}"/"${prefix}"_*)
  done
  shopt -u nullglob
}

cleanup() {
  prune_prefix "${HERMES_BACKUP_ROOT}/daily/pg" "hermes" "${HERMES_DAILY_KEEP}"
  prune_prefix "${HERMES_BACKUP_ROOT}/daily/data" "hermes_data" "${HERMES_DAILY_KEEP}"
  prune_prefix "${HERMES_BACKUP_ROOT}/weekly/pg" "hermes" "${HERMES_WEEKLY_KEEP}"
  prune_prefix "${HERMES_BACKUP_ROOT}/weekly/data" "hermes_data" "${HERMES_WEEKLY_KEEP}"
}

restore_db() {
  local dump_file="$1"
  [[ -f "${dump_file}" ]] || { echo "备份文件不存在：${dump_file}" >&2; exit 1; }
  echo "将覆盖 ${HERMES_PG_DB} 中的现有数据。"
  read -r -p "确认恢复？[y/N] " answer
  [[ "${answer}" == "y" ]] || { echo "已取消"; exit 0; }
  docker exec -i "${HERMES_DB_CONTAINER}" \
    pg_restore -U "${HERMES_PG_USER}" -d "${HERMES_PG_DB}" \
    --clean --if-exists --no-owner < "${dump_file}"
  log "数据库恢复完成"
}

case "${1:-backup}" in
  --restore-db) restore_db "${2:?需要备份文件路径}" ;;
  backup) backup_db && backup_data && create_weekly_copy && cleanup && log "备份全部完成" ;;
  *) echo "用法: ./scripts/backup.sh [backup|--restore-db <file>]" >&2; exit 2 ;;
esac
