#!/bin/sh
set -eu

image="${1:-tstrings-kali-agent}"
limit_bytes="${MAX_IMAGE_BYTES:-2147483648}"

size_bytes="$(docker image inspect "$image" --format '{{.Size}}')"
size_mb="$((size_bytes / 1024 / 1024))"
limit_mb="$((limit_bytes / 1024 / 1024))"

printf '%s\n' "Image: $image" "Size: ${size_mb} MiB" "Limit: ${limit_mb} MiB"
if [ "$size_bytes" -gt "$limit_bytes" ]; then
  echo "ERROR: image exceeds the configured size limit" >&2
  exit 1
fi

echo "OK: image is within the size limit"
