#!/usr/bin/env bash
# install_matcher.sh - set up the road matcher service on the laptop:
#   1. a Python virtual environment in matcher/.venv with the pinned packages
#   2. a new random password for the road_matcher database role, saved in matcher/matcher.env
#      (gitignored, readable by you only)
#   3. the systemd service road-matcher, started now and on every boot
# Safe to run again (e.g. after pulling a new road_matcher.py, or to change the password).
# Never prints the password.
#
# Run on the laptop as your normal user (it asks for sudo for step 3), with Supabase started
# and all migrations applied:
#     cd ~/ee475aut26team5/Backend && bash matcher/install_matcher.sh

set -euo pipefail
cd "$(dirname "$0")/.."   # run from Backend/
BACKEND=$(pwd)

GREEN=$'\e[32m'; RED=$'\e[31m'; BOLD=$'\e[1m'; RESET=$'\e[0m'
die() { echo "${RED}$1${RESET}"; exit 1; }

[[ $EUID -ne 0 ]] || die "Run this as your normal user, not with sudo."
[[ $BACKEND != *" "* ]] || die "The repo path has a space in it; systemd needs a path without spaces."
command -v python3 >/dev/null || die "python3 is missing: sudo apt install -y python3 python3-venv"
python3 -c 'import venv, ensurepip' 2>/dev/null || die "python3-venv is missing: sudo apt install -y python3-venv"
command -v psql >/dev/null || die "psql is missing: sudo apt install -y postgresql-client"

status_env=$(supabase status -o env 2>/dev/null) || die "Supabase isn't running. Run: supabase start"
DB_URL=$(printf '%s\n' "$status_env" | grep -E '^DB_URL=' | head -1 | cut -d= -f2- | tr -d '"' | sed 's/[[:space:]]*$//')
[[ -n $DB_URL ]] || die "Couldn't read DB_URL from supabase status."
DB_PORT=$(printf '%s' "$DB_URL" | sed -E 's#.*@[^:/]+:([0-9]+)/.*#\1#')
[[ $DB_PORT =~ ^[0-9]+$ ]] || DB_PORT=54322

psql "$DB_URL" -qAtc "select 1 from pg_roles where rolname = 'road_matcher'" | grep -q 1 \
    || die "The road_matcher role is missing. Run: supabase migration up"

echo "${BOLD}1. Python environment${RESET}"
python3 -m venv matcher/.venv
matcher/.venv/bin/pip install --quiet --disable-pip-version-check -r matcher/requirements.txt
matcher/.venv/bin/python -c 'import psycopg' || die "psycopg didn't install."
echo "  ok"

echo "${BOLD}2. Database password${RESET}"
PW=$(python3 -c 'import secrets; print(secrets.token_hex(24))')
# Sent on stdin, so it never appears in the process list.
printf "alter role road_matcher with login password '%s';\n" "$PW" \
    | psql "$DB_URL" -q -v ON_ERROR_STOP=1 >/dev/null \
    || die "Couldn't set the password (see the error above)."
( umask 077
  cat > matcher/matcher.env <<EOF
# Written by install_matcher.sh. Gitignored. Never commit or share.
MATCHER_DB_URL=postgresql://road_matcher:${PW}@127.0.0.1:${DB_PORT}/postgres
VALHALLA_URL=http://127.0.0.1:8002
EOF
)
chmod 600 matcher/matcher.env
unset PW
git check-ignore -q matcher/matcher.env || die "matcher/matcher.env is NOT gitignored. Fix Backend/.gitignore before going on."
echo "  ok (saved in matcher/matcher.env, mode 600)"

echo "${BOLD}3. systemd service${RESET}"
sed -e "s#@USER@#$(id -un)#g" -e "s#@BACKEND@#$BACKEND#g" matcher/road-matcher.service \
    | sudo tee /etc/systemd/system/road-matcher.service >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable --quiet road-matcher
sudo systemctl restart road-matcher
sleep 3
if systemctl is-active --quiet road-matcher; then
    echo "  ok (running)"
else
    die "The service didn't start. See: journalctl -u road-matcher -n 50"
fi

echo
echo "${GREEN}${BOLD}Road matcher installed.${RESET} Watch it with: journalctl -u road-matcher -f"
echo "Then run: bash checks/check_matcher.sh"
