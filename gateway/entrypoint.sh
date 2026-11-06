#!/bin/sh
# Render the runtime config from secrets in the environment, then exec KrakenD.
# Nothing secret is committed or baked into an image: /tmp is a tmpfs and files are 0600.
set -eu
umask 077

: "${GATEWAY_SECRET:?GATEWAY_SECRET is required}"
: "${JWT_SIGNING_KEY:?JWT_SIGNING_KEY is required}"
case "$GATEWAY_SECRET$JWT_SIGNING_KEY" in
  *[!A-Za-z0-9]*) echo "secrets must be alphanumeric (make env generates hex)" >&2; exit 1 ;;
esac
[ "${#JWT_SIGNING_KEY}" -ge 32 ] || { echo "JWT_SIGNING_KEY too short" >&2; exit 1; }
[ "${#GATEWAY_SECRET}" -ge 16 ] || { echo "GATEWAY_SECRET too short" >&2; exit 1; }

# HS256 verification key as a single symmetric JWK (kid must match gateway/mint_token.py)
k=$(printf '%s' "$JWT_SIGNING_KEY" | base64 | tr -d '\n=' | tr '+/' '-_')
printf '{"keys":[{"kty":"oct","kid":"gw-1","alg":"HS256","use":"sig","k":"%s"}]}\n' "$k" >/tmp/jwks.json

# inject the shared secret the API expects
sed "s/\${GATEWAY_SECRET}/${GATEWAY_SECRET}/g" /gateway/krakend.json >/tmp/krakend.json
if grep -q '\${' /tmp/krakend.json; then
  echo "unrendered placeholder left in krakend.json" >&2
  exit 1
fi

exec krakend run -c /tmp/krakend.json
