# MinIO with a static busybox so compose can run a real in-container healthcheck
# (the upstream-style distroless image has no shell, curl or wget).
FROM busybox:1.37-musl@sha256:f6a95d611e0a8780fabdb2aec43e31275b2720dfefa3e41b16835bfc42454478 AS bb

FROM cgr.dev/chainguard/minio:latest@sha256:cee9a8e35885e93f14b3cb32960e5017aff74e2adfb52ba87c65003b0163886b
COPY --from=bb /bin/busybox /busybox
