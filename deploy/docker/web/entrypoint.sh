#!/bin/sh
# Render the gateway upstream and (optional) Authorization header from the environment into /tmp.
# Values are validated so they cannot inject nginx directives.
set -eu

upstream="${GATEWAY_UPSTREAM:-http://krakend:8080}"
case "$upstream" in
  *[!A-Za-z0-9.:/_-]*) echo "web-entrypoint: invalid GATEWAY_UPSTREAM" >&2; exit 64 ;;
esac
case "$upstream" in
  http://*|https://*) ;;
  *) echo "web-entrypoint: GATEWAY_UPSTREAM must be http(s)://host[:port]" >&2; exit 64 ;;
esac

auth=""
jwt="${WEB_API_JWT:-}"
if [ -n "$jwt" ]; then
  case "$jwt" in
    *[!A-Za-z0-9._-]*) echo "web-entrypoint: WEB_API_JWT is not a valid JWT" >&2; exit 64 ;;
  esac
  auth="Bearer $jwt"
fi

umask 077
{
  printf 'set $gateway_upstream "%s";\n' "$upstream"
  printf 'set $gateway_authorization "%s";\n' "$auth"
} > /tmp/web-upstream.conf

exec "$@"
