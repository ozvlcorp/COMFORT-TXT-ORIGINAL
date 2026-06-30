#!/usr/bin/env bash
# =============================================================================
# safe_deploy.sh — SAFE, RE-RUNNABLE deploy for Comfort Textile Bot
# -----------------------------------------------------------------------------
# Run this ON THE SERVER (the owner runs it). It:
#   1. Pre-flight checks (git repo + .env present)
#   2. Auto-detects install type (systemd "comfort-bot" OR docker compose "bot")
#   3. Records the current commit (PREV_SHA) for rollback
#   4. Backs up the SQLite DB (consistent online backup) BEFORE touching anything
#   5. Updates code with a FAST-FORWARD-ONLY pull (never destroys local work)
#   6. Rebuilds/restarts the service
#   7. Runs a bounded health check; ROLLS BACK to PREV_SHA if unhealthy
#   8. Registers MoySklad webhooks (idempotent) only after a healthy restart
#
# DB migrations in database.py are additive & idempotent (ALTER ADD COLUMN in
# try/except, CREATE ... IF NOT EXISTS). A normal deploy cannot lose data; the
# DB backup exists purely as a rollback safety net.
#
# Usage:
#   sudo bash deploy/safe_deploy.sh
#   APP_DIR=/opt/comfort-bot sudo -E bash deploy/safe_deploy.sh   # override app dir
# =============================================================================
set -euo pipefail

# ─── Config ───────────────────────────────────────────────────────────────────
APP_DIR="${APP_DIR:-/opt/comfort-bot}"
BRANCH="main"
SERVICE_NAME="comfort-bot"          # systemd unit name
COMPOSE_SERVICE="bot"               # docker compose service name
HEALTH_TIMEOUT="${HEALTH_TIMEOUT:-40}"   # seconds to wait for healthy markers
# Log markers that indicate the bot booted correctly
# (Caches initialized → moysklad_api.py; Starting bot polling → bot.py).
HEALTH_MARKERS='Caches initialized|Starting bot polling'

# ─── Colors / loggers ─────────────────────────────────────────────────────────
if [ -t 1 ]; then
  G='\033[1;32m'; Y='\033[1;33m'; R='\033[1;31m'; B='\033[1;34m'; C='\033[1;36m'; N='\033[0m'
else
  G=''; Y=''; R=''; B=''; C=''; N=''
fi
STEP=0
step() { STEP=$((STEP+1)); echo -e "\n${C}━━━ [${STEP}] $* ━━━${N}"; }
info() { echo -e "${B}[..]${N}  $*"; }
ok()   { echo -e "${G}[OK]${N}  $*"; }
warn() { echo -e "${Y}[!!]${N}  $*"; }
die()  { echo -e "${R}[ERR]${N} $*" >&2; exit 1; }

# Populated as we go so the summary can report them.
PREV_SHA=""
BACKUP_FILE=""
INSTALL_TYPE=""
COMPOSE_BIN=""      # "docker compose" (v2) or "docker-compose" (v1)

# =============================================================================
# STEP 1 — Pre-flight
# =============================================================================
step "Pre-flight checks"

[ -d "$APP_DIR" ]      || die "APP_DIR does not exist: $APP_DIR"
[ -d "$APP_DIR/.git" ] || die "Not a git repository: $APP_DIR (no .git). Refusing to run."
[ -f "$APP_DIR/.env" ] || die ".env not found at $APP_DIR/.env. Refusing to run."
command -v git >/dev/null 2>&1 || die "git is not installed."

info "APP_DIR ........ $APP_DIR"
info "Branch ......... $BRANCH"
info ".env ........... present"
info "Current commit . $(git -C "$APP_DIR" rev-parse --short HEAD 2>/dev/null || echo '?')"
ok "Pre-flight checks passed."

# =============================================================================
# STEP 2 — Auto-detect install type (systemd vs docker). No guessing.
# =============================================================================
step "Detecting install type"

has_systemd=false
has_docker=false

# systemd: the unit file must actually be INSTALLED (not merely transient).
if command -v systemctl >/dev/null 2>&1; then
  if systemctl list-unit-files "${SERVICE_NAME}.service" >/dev/null 2>&1 \
     && systemctl list-unit-files 2>/dev/null | grep -q "^${SERVICE_NAME}\.service"; then
    has_systemd=true
  fi
fi

# docker: detect compose v2 ("docker compose") OR legacy v1 ("docker-compose").
if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
  COMPOSE_BIN="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then
  COMPOSE_BIN="docker-compose"
fi
if [ -n "$COMPOSE_BIN" ] && [ -f "$APP_DIR/docker-compose.yml" ]; then
  if $COMPOSE_BIN -f "$APP_DIR/docker-compose.yml" config --services 2>/dev/null \
       | grep -qx "$COMPOSE_SERVICE"; then
    has_docker=true
  fi
fi

if [ "$has_systemd" = true ] && [ "$has_docker" = true ]; then
  die "Both a systemd '${SERVICE_NAME}' unit AND a docker compose '${COMPOSE_SERVICE}' service were detected.
       Cannot safely guess which one is live. Stop/remove the one you are NOT using, then re-run."
fi
if [ "$has_systemd" = false ] && [ "$has_docker" = false ]; then
  die "Could not detect a systemd '${SERVICE_NAME}' unit or a docker compose '${COMPOSE_SERVICE}' service.
       Is this the right APP_DIR? Was the bot installed via deploy/deploy.sh (systemd) or deploy/setup.sh (docker)?"
fi

if [ "$has_systemd" = true ]; then
  INSTALL_TYPE="systemd"
else
  INSTALL_TYPE="docker"
fi
ok "Install type detected: ${INSTALL_TYPE}${COMPOSE_BIN:+ (via '$COMPOSE_BIN')}"

# Helper: run compose against the repo-root compose file (v1 or v2).
dc() { $COMPOSE_BIN -f "$APP_DIR/docker-compose.yml" "$@"; }

# =============================================================================
# STEP 3 — Record current commit for rollback
# =============================================================================
step "Recording current commit (for rollback)"
PREV_SHA="$(git -C "$APP_DIR" rev-parse HEAD)"
ok "PREV_SHA = $PREV_SHA"

# =============================================================================
# STEP 4 — Back up the SQLite DB BEFORE restart / migrations
# =============================================================================
step "Backing up the database"

BACKUP_DIR="$APP_DIR/backups"
mkdir -p "$BACKUP_DIR"
TS="$(date +%Y%m%d-%H%M%S)"
BACKUP_FILE="$BACKUP_DIR/comfort_bot-${INSTALL_TYPE}-${TS}.db"

# Read a value from .env WITHOUT sourcing it (never execute arbitrary content).
env_get() {
  local key="$1" line val
  line="$(grep -E "^[[:space:]]*${key}=" "$APP_DIR/.env" | tail -n1 || true)"
  [ -n "$line" ] || return 0
  val="${line#*=}"
  val="${val%$'\r'}"
  val="${val%\"}"; val="${val#\"}"
  val="${val%\'}"; val="${val#\'}"
  printf '%s' "$val"
}

# Consistent online SQLite backup via a Python interpreter (handles a live
# writer / WAL correctly) + integrity check. Reads from stdin so paths with
# spaces are passed safely as argv. Returns non-zero unless integrity == "ok".
PY_BACKUP='
import sqlite3, sys
src, dst = sys.argv[1], sys.argv[2]
s = sqlite3.connect(src); d = sqlite3.connect(dst)
try:
    with d:
        s.backup(d)
    chk = d.execute("PRAGMA integrity_check").fetchone()[0]
finally:
    d.close(); s.close()
sys.exit(0 if chk == "ok" else 3)
'

backup_succeeded=false

if [ "$INSTALL_TYPE" = "docker" ]; then
  CONTAINER_DB="$(env_get DB_PATH)"
  CONTAINER_DB="${CONTAINER_DB:-/data/comfort_bot.db}"
  info "Container DB path: $CONTAINER_DB"

  # SAFETY: the DB MUST live under the persistent bot_data volume (mounted at
  # /data). Anything else sits on the container's ephemeral writable layer and
  # would be DESTROYED by `up --build`. Refuse to proceed — no silent data loss.
  case "$CONTAINER_DB" in
    /data|/data/*) : ;;
    *) die "DB_PATH ($CONTAINER_DB) is NOT under /data (the persistent bot_data volume).
            It would live on the container's ephemeral writable layer and be LOST on the next
            'docker compose up --build'. Fix before deploying:
              1) set  DB_PATH=/data/comfort_bot.db  in $APP_DIR/.env
              2) move the existing DB into the bot_data volume
            Then re-run this script." ;;
  esac

  CID="$(dc ps -q "$COMPOSE_SERVICE" 2>/dev/null || true)"

  if [ -n "$CID" ] && docker exec "$CID" test -f "$CONTAINER_DB" 2>/dev/null; then
    # Container running → consistent online backup via its own Python.
    CPY=python
    docker exec "$CID" sh -c 'command -v python >/dev/null 2>&1' 2>/dev/null || CPY=python3
    info "Consistent online backup via container $CPY…"
    if docker exec -i "$CID" "$CPY" - "$CONTAINER_DB" /tmp/_safe_backup.db <<PY
$PY_BACKUP
PY
    then
      if docker cp "$CID:/tmp/_safe_backup.db" "$BACKUP_FILE"; then
        backup_succeeded=true
      fi
      docker exec "$CID" rm -f /tmp/_safe_backup.db 2>/dev/null || true
    fi
    if [ "$backup_succeeded" = false ]; then
      warn "Online backup failed; falling back to a plain file copy from the container."
      docker cp "$CID:$CONTAINER_DB" "$BACKUP_FILE" && backup_succeeded=true
    fi
  else
    # Container down → no live writer, a plain copy from the volume is consistent.
    info "Bot container not running; copying DB straight from the volume…"
    PROJECT="${COMPOSE_PROJECT_NAME:-$(basename "$APP_DIR" | tr '[:upper:]' '[:lower:]' | tr -cd 'a-z0-9_-')}"
    REAL_VOL="${PROJECT}_bot_data"
    if ! docker volume inspect "$REAL_VOL" >/dev/null 2>&1; then
      # Fall back to a suffix match, but abort if it is ambiguous.
      mapfile -t _vols < <(docker volume ls --format '{{.Name}}' 2>/dev/null | grep -E '_bot_data$' || true)
      if [ "${#_vols[@]}" -eq 1 ]; then
        REAL_VOL="${_vols[0]}"
      elif [ "${#_vols[@]}" -gt 1 ]; then
        die "Multiple '*_bot_data' volumes found: ${_vols[*]}. Set COMPOSE_PROJECT_NAME and re-run."
      else
        REAL_VOL=""
      fi
    fi
    VOL_DB="/data/$(basename "$CONTAINER_DB")"
    if [ -n "$REAL_VOL" ] && docker run --rm \
         -v "$REAL_VOL:/data:ro" -v "$BACKUP_DIR:/backup" alpine:3 \
         sh -c "test -f '$VOL_DB' && cp '$VOL_DB' '/backup/$(basename "$BACKUP_FILE")'"; then
      backup_succeeded=true
    fi
  fi
else
  # systemd: DB_PATH from .env, absolute or relative to APP_DIR.
  DBP="$(env_get DB_PATH)"
  DBP="${DBP:-comfort_bot.db}"
  case "$DBP" in
    /*) HOST_DB="$DBP" ;;
    *)  HOST_DB="$APP_DIR/$DBP" ;;
  esac
  info "Resolved DB path: $HOST_DB"

  if [ -f "$HOST_DB" ]; then
    PYBIN=""
    if [ -x "$APP_DIR/venv/bin/python" ]; then PYBIN="$APP_DIR/venv/bin/python"
    elif command -v python3 >/dev/null 2>&1; then PYBIN="python3"
    elif command -v python  >/dev/null 2>&1; then PYBIN="python"
    fi
    if [ -n "$PYBIN" ]; then
      info "Consistent online backup via $PYBIN…"
      if "$PYBIN" - "$HOST_DB" "$BACKUP_FILE" <<PY
$PY_BACKUP
PY
      then backup_succeeded=true
      fi
    fi
    if [ "$backup_succeeded" = false ]; then
      warn "Online backup unavailable; falling back to a plain file copy (+ WAL/SHM if present)."
      cp "$HOST_DB" "$BACKUP_FILE" && backup_succeeded=true
      for sidecar in -wal -shm -journal; do
        [ -f "${HOST_DB}${sidecar}" ] && cp "${HOST_DB}${sidecar}" "${BACKUP_FILE}${sidecar}" || true
      done
    fi
  fi
fi

if [ "$backup_succeeded" = true ]; then
  ok "DB backed up to: $BACKUP_FILE"
else
  warn "No existing DB file found (fresh install?) — nothing to back up. Continuing."
  BACKUP_FILE="(none — no DB present yet)"
fi

# =============================================================================
# STEP 5 — Update code (fast-forward only; never discard local work)
# =============================================================================
step "Updating code (fast-forward only)"

info "git fetch origin $BRANCH…"
git -C "$APP_DIR" fetch origin "$BRANCH"

info "git checkout $BRANCH…"
git -C "$APP_DIR" checkout "$BRANCH"

# Refuse to clobber uncommitted changes.
if ! git -C "$APP_DIR" diff --quiet || ! git -C "$APP_DIR" diff --cached --quiet; then
  die "Uncommitted changes present in $APP_DIR. Refusing to pull (could lose work).
       Commit/stash them, or inspect with: git -C '$APP_DIR' status"
fi

info "git pull --ff-only origin $BRANCH…"
if ! git -C "$APP_DIR" pull --ff-only origin "$BRANCH"; then
  die "Fast-forward pull failed (local branch has diverged from origin/$BRANCH).
       NOT resetting --hard. Resolve manually:  git -C '$APP_DIR' status
       Your DB backup is safe at: $BACKUP_FILE"
fi
NEW_SHA="$(git -C "$APP_DIR" rev-parse HEAD)"
if [ "$NEW_SHA" = "$PREV_SHA" ]; then
  ok "Already up to date ($(git -C "$APP_DIR" rev-parse --short HEAD)). Restarting anyway (idempotent)."
else
  ok "Updated $PREV_SHA → $NEW_SHA"
fi

# =============================================================================
# Restart helpers + rollback
# =============================================================================
restart_service() {
  if [ "$INSTALL_TYPE" = "docker" ]; then
    info "$COMPOSE_BIN up --build -d…"
    dc up --build -d || return 1
  else
    if [ -x "$APP_DIR/venv/bin/pip" ]; then
      info "Installing Python dependencies (pip)…"
      if ! "$APP_DIR/venv/bin/pip" install -r "$APP_DIR/requirements.txt" -q; then
        warn "pip install failed — not restarting (deps may be incomplete)."
        return 1
      fi
    else
      warn "venv pip not found at $APP_DIR/venv/bin/pip — skipping pip install."
    fi
    info "systemctl restart $SERVICE_NAME…"
    systemctl restart "$SERVICE_NAME" || return 1
  fi
}

# Returns 0 if healthy within HEALTH_TIMEOUT, 1 otherwise.
# Markers are RE-EVALUATED every iteration (never latched). For docker we read
# the (freshly recreated) container's own logs via --tail; for systemd we anchor
# journalctl to the moment the restart was issued so a stale marker from a
# previous boot cannot make a crash-looping deploy look healthy.
health_check() {
  local since_human="$1"
  local deadline running_ok markers_ok
  deadline=$(( $(date +%s) + HEALTH_TIMEOUT ))
  running_ok=false
  markers_ok=false
  info "Health check (up to ${HEALTH_TIMEOUT}s): markers [$HEALTH_MARKERS]…"
  while [ "$(date +%s)" -lt "$deadline" ]; do
    running_ok=false
    markers_ok=false
    if [ "$INSTALL_TYPE" = "docker" ]; then
      # A crash-looping container reports "restarting", not "running".
      if dc ps "$COMPOSE_SERVICE" 2>/dev/null | grep -Eq '\brunning\b|\bUp\b'; then
        running_ok=true
      fi
      # `up --build` recreates the container, so its logs start fresh — --tail
      # cannot surface a previous container's startup line.
      if dc logs --tail=400 "$COMPOSE_SERVICE" 2>/dev/null | grep -Eq "$HEALTH_MARKERS"; then
        markers_ok=true
      fi
    else
      if systemctl is-active --quiet "$SERVICE_NAME"; then
        running_ok=true
      fi
      if journalctl -u "$SERVICE_NAME" --since "$since_human" --no-pager 2>/dev/null \
           | grep -Eq "$HEALTH_MARKERS"; then
        markers_ok=true
      fi
    fi
    if [ "$running_ok" = true ] && [ "$markers_ok" = true ]; then
      ok "Service is running and post-restart startup markers were found."
      return 0
    fi
    sleep 2
  done
  warn "Health check did NOT pass within ${HEALTH_TIMEOUT}s (running=$running_ok, markers=$markers_ok)."
  return 1
}

# Timestamp captured immediately BEFORE issuing the restart, used to scope the
# systemd health-check log scan. (1s of slack so a same-second marker counts.)
restart_since() { date -d "@$(( $(date +%s) - 1 ))" '+%Y-%m-%d %H:%M:%S' 2>/dev/null || date '+%Y-%m-%d %H:%M:%S'; }

rollback() {
  step "ROLLBACK — restoring previous commit $PREV_SHA"
  warn "Deploy was unhealthy. Reverting code to $PREV_SHA and restarting."
  git -C "$APP_DIR" checkout "$PREV_SHA" || warn "git checkout $PREV_SHA failed — fix manually."
  local since; since="$(restart_since)"
  if restart_service && health_check "$since"; then
    warn "ROLLBACK COMPLETE — previous version $PREV_SHA is back up and healthy."
  else
    die "ROLLBACK RESTART UNHEALTHY — bot may be DOWN. Manual intervention required.
         Previous commit: $PREV_SHA   DB backup: $BACKUP_FILE"
  fi
  warn "  DB backup (if any) available at: $BACKUP_FILE"
  exit 1
}

# =============================================================================
# STEP 6 — Restart
# =============================================================================
step "Restarting the service ($INSTALL_TYPE)"
# Do NOT let `set -e` abort here — a failed build/pip/restart must route into
# rollback() rather than leaving the bot down on new code. `if !` neutralises set -e.
SINCE="$(restart_since)"
if ! restart_service; then
  warn "Restart/build failed — rolling back to previous commit."
  rollback
fi
ok "Restart command issued."

# =============================================================================
# STEP 7 — Health check (rollback on failure)
# =============================================================================
step "Health check"
if ! health_check "$SINCE"; then
  rollback
fi
ok "Service is healthy."

# =============================================================================
# STEP 8 — Register MoySklad webhooks (idempotent)
# =============================================================================
step "Registering MoySklad webhooks (idempotent)"
webhook_rc=0
if [ "$INSTALL_TYPE" = "docker" ]; then
  dc exec -T "$COMPOSE_SERVICE" python register_webhooks.py || webhook_rc=$?
else
  if [ -x "$APP_DIR/venv/bin/python" ]; then
    ( cd "$APP_DIR" && "$APP_DIR/venv/bin/python" register_webhooks.py ) || webhook_rc=$?
  else
    warn "venv python not found at $APP_DIR/venv/bin/python — skipping webhook registration."
  fi
fi
if [ "$webhook_rc" -eq 0 ]; then
  ok "Webhook registration finished ('already exists' lines are expected/fine)."
else
  warn "register_webhooks.py exited non-zero ($webhook_rc). The bot is healthy and running;"
  warn "this is usually a transient MoySklad/API issue — re-run the script or rerun webhooks manually."
fi

# =============================================================================
# Final summary
# =============================================================================
echo ""
echo -e "${G}════════════════════════════════════════════════════════════${N}"
echo -e "${G}  Deploy complete — service is healthy.${N}"
echo -e "${G}════════════════════════════════════════════════════════════${N}"
echo -e "  Install type ...... ${INSTALL_TYPE}"
echo -e "  App dir ........... ${APP_DIR}"
echo -e "  Now at commit ..... $(git -C "$APP_DIR" rev-parse --short HEAD)"
echo -e "  Previous commit ... ${PREV_SHA}"
echo -e "  DB backup ......... ${BACKUP_FILE}"
echo ""
echo -e "  Manual rollback if ever needed:"
echo -e "    git -C '$APP_DIR' checkout ${PREV_SHA}"
if [ "$INSTALL_TYPE" = "docker" ]; then
  echo -e "    $COMPOSE_BIN -f '$APP_DIR/docker-compose.yml' up --build -d"
  echo -e "  Logs: $COMPOSE_BIN -f '$APP_DIR/docker-compose.yml' logs -f ${COMPOSE_SERVICE}"
else
  echo -e "    systemctl restart ${SERVICE_NAME}"
  echo -e "  Logs: journalctl -u ${SERVICE_NAME} -f"
fi
echo ""
