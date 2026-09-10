#!/bin/sh
set -eu
umask 077
fail() { echo 'Invalid controlled Web deployment configuration' >&2; exit 2; }
test "${AGENT_UI_MODE:-}" = "$(cat /opt/agent/ui-mode)" || fail
case "$AGENT_UI_MODE" in agent|legacy) ;; *) fail;; esac
case "${AGENT_PUBLIC_ORIGIN:-}" in *[!a-z0-9:./-]*) fail;; esac
AGENT_PROXY_API_KEY="${AGENT_PROXY_API_KEY:-}"
export AGENT_PROXY_API_KEY
case "$AGENT_PROXY_API_KEY" in *[!A-Za-z0-9._-]*) fail;; esac
test "${#AGENT_PROXY_API_KEY}" -ge 16 && test "${#AGENT_PROXY_API_KEY}" -le 256 || fail
AGENT_TRANSPORT_MODE="${AGENT_TRANSPORT_MODE:-https}"
AGENT_LISTEN_OPTIONS=ssl
case "$AGENT_TRANSPORT_MODE" in
  https)
    printf '%s\n' "$AGENT_PUBLIC_ORIGIN" | grep -Eq '^https://[a-z0-9][a-z0-9.-]*(:[0-9]{1,5})?$' || fail
    test -r /run/tls/server.crt && test -r /run/tls/server.key || fail;;
  private-http)
    printf '%s\n' "$AGENT_PUBLIC_ORIGIN" | grep -Eq '^http://[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+(:[0-9]{1,5})?$' || fail
    authority="${AGENT_PUBLIC_ORIGIN#http://}"
    printf '%s\n' "${authority%%:*}" | awk -F. '
      NF != 4 { exit 1 }
      { for (i=1; i<=4; i++) if ($i > 255 || (length($i)>1 && substr($i,1,1)=="0")) exit 1 }
      !($1==10 || $1==127 || ($1==172 && $2>=16 && $2<=31) || ($1==192 && $2==168)) { exit 1 }
    ' || fail
    AGENT_LISTEN_OPTIONS=;;
  *) fail;;
esac
AGENT_PUBLIC_AUTHORITY="${AGENT_PUBLIC_ORIGIN#*://}"
export AGENT_PUBLIC_AUTHORITY AGENT_TRANSPORT_MODE AGENT_LISTEN_OPTIONS
case "$AGENT_PUBLIC_AUTHORITY" in
  *:*) port="${AGENT_PUBLIC_AUTHORITY##*:}"; test "$port" -ge 1 && test "$port" -le 65535 || fail;;
esac
AGENT_READ_TIMEOUT=40s
if [ "$AGENT_UI_MODE" = legacy ]; then AGENT_READ_TIMEOUT=125s; fi
if [ -n "${AGENT_READ_TIMEOUT_SECONDS:-}" ]; then
  case "$AGENT_READ_TIMEOUT_SECONDS" in *[!0-9]*) fail;; esac
  test "$AGENT_READ_TIMEOUT_SECONDS" -ge 40 && test "$AGENT_READ_TIMEOUT_SECONDS" -le 135 || fail
  AGENT_READ_TIMEOUT="${AGENT_READ_TIMEOUT_SECONDS}s"
fi
export AGENT_READ_TIMEOUT
envsubst '${AGENT_PUBLIC_AUTHORITY} ${AGENT_UI_MODE} ${AGENT_TRANSPORT_MODE} ${AGENT_LISTEN_OPTIONS}' < /opt/agent/nginx/nginx.conf.template > /tmp/agent-nginx.conf
envsubst '${AGENT_PROXY_API_KEY} ${AGENT_READ_TIMEOUT}' < /opt/agent/nginx/proxy.conf.template > /tmp/agent-proxy.conf
nginx -t -c /tmp/agent-nginx.conf >/dev/null 2>&1 || fail
exec nginx -c /tmp/agent-nginx.conf -g 'daemon off;'
