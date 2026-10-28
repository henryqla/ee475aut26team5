#!/usr/bin/env bash
# check_keys.sh - is it safe to turn on Tailscale Funnel?
# Checks that the custom signing key is loaded and that the CLI's built-in default keys (the
# same on every install) have been replaced. Prints PASS/FAIL only; never prints a key.
#
# Run on the laptop, with Supabase started:
#     cd ~/ee475aut26team5/Backend && bash check_keys.sh

set -u
cd "$(dirname "$0")"

API=http://127.0.0.1:54321
KEYS_FILE=supabase/signing_keys.json
GREEN=$'\e[32m'; RED=$'\e[31m'; YELLOW=$'\e[33m'; BOLD=$'\e[1m'; RESET=$'\e[0m'
failures=0

pass() { echo "  ${GREEN}PASS${RESET}  $1"; }
fail() { echo "  ${RED}FAIL${RESET}  $1"; failures=$((failures + 1)); }
info() { echo "  ${YELLOW}INFO${RESET}  $1"; }

status_env=$(supabase status -o env 2>/dev/null) || {
    echo "${RED}Supabase isn't running.${RESET} Run: supabase start"; exit 1; }
env_value() { printf '%s\n' "$status_env" | grep -E "^$1=" | head -1 | cut -d= -f2- | tr -d '"'; }

# HTTP status of an admin-only request (list auth users) made with the given key.
admin_status() {
    local headers=(-H "apikey: $1")
    [[ $1 == eyJ* ]] && headers+=(-H "Authorization: Bearer $1")
    curl -s -m 5 -o /dev/null -w '%{http_code}' "$API/auth/v1/admin/users" "${headers[@]}"
}

echo "${BOLD}1. Config${RESET}"
section=$(awk '/^\[/{s=$0} /^[[:space:]]*signing_keys_path[[:space:]]*=/{print s}' supabase/config.toml)
if [[ $section == "[auth]" ]]; then pass "signing_keys_path is set under [auth]"
else fail "signing_keys_path is not set (uncommented) under [auth] in supabase/config.toml"; fi

file_kids=$(python3 - "$KEYS_FILE" 2>/dev/null <<'EOF'
import json, sys
keys = json.load(open(sys.argv[1]))
if not isinstance(keys, list):
    sys.exit("not a list")
print(" ".join(k.get("kid", "") for k in keys))
EOF
)
if [[ -n $file_kids ]]; then pass "$KEYS_FILE exists and is a JSON list of keys"
else fail "$KEYS_FILE is missing, not valid JSON, or not wrapped in [ ]"; fi

echo "${BOLD}2. Is the signing key used for sign-ins?${RESET}"
public_key=$(env_value PUBLISHABLE_KEY); [[ -z $public_key ]] && public_key=$(env_value ANON_KEY)
token_header=$(curl -s -m 5 -X POST "$API/auth/v1/signup" -H "apikey: $public_key" \
        -H "Content-Type: application/json" -d '{}' | python3 -c '
import base64, json, sys
token = json.load(sys.stdin).get("access_token", "")
part = token.split(".")[0]
header = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
print(header.get("alg", ""), header.get("kid", ""))' 2>/dev/null)
read -r alg kid <<<"$token_header"
if [[ -z $alg ]]; then fail "anonymous sign-in failed (is enable_anonymous_sign_ins = true? restarted?)"
elif [[ $alg == ES256 && " $file_kids " == *" $kid "* ]]; then pass "sign-in tokens are signed with your key ($alg)"
else fail "sign-in tokens are signed with $alg, not your key"; fi

echo "${BOLD}3. Are the built-in default keys replaced?${RESET}"
# Unset keys fall back to values built into the CLI, the same on every install. Each one must
# come from Backend/.env instead (SUPABASE_SETUP.txt Step 9B).
dotenv_value() { grep -E "^$1=" .env 2>/dev/null | head -1 | cut -d= -f2- | tr -d '"'; }
if [[ -f .env ]]; then pass "Backend/.env exists"
else fail "Backend/.env is missing (Step 9B)"; fi

check_custom() {   # $1 = name in supabase status, $2 = name in .env
    local live wanted; live=$(env_value "$1"); wanted=$(dotenv_value "$2")
    if [[ -z $live ]]; then info "$1 not shown by supabase status; skipped"
    elif [[ -n $wanted && $live == "$wanted" ]]; then pass "$1 is your own value from .env"
    else fail "$1 is not the value in .env (still the built-in default?)"; fi
}
check_custom JWT_SECRET      SUPABASE_AUTH_JWT_SECRET
check_custom PUBLISHABLE_KEY SUPABASE_AUTH_PUBLISHABLE_KEY
check_custom SECRET_KEY      SUPABASE_AUTH_SECRET_KEY

echo "${BOLD}4. Admin access${RESET}"
code=$(admin_status sb_secret_notarealkey123)
if [[ $code == 401 || $code == 403 ]]; then pass "a made-up key is rejected ($code)"
else fail "a made-up key got $code (expected 401/403)"; fi

for name in SECRET_KEY SERVICE_ROLE_KEY; do
    key=$(env_value "$name")
    [[ -z $key ]] && continue
    code=$(admin_status "$key")
    if [[ $code == 200 ]]; then pass "your $name works for admin tasks ($code)"
    else fail "your $name was refused ($code); you'd have no admin key"; fi
done

echo
if (( failures == 0 )); then echo "${GREEN}${BOLD}All checks passed: safe to turn on Funnel.${RESET}"
else echo "${RED}${BOLD}$failures check(s) failed: don't turn on Funnel yet.${RESET} Send this output (it contains no keys)."; fi
