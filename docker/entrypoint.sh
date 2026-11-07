#!/bin/sh
set -eu
mkdir -p /data/ssh /data/uploads
chmod 700 /data/ssh /data/uploads
if [ ! -e /data/ssh/known_hosts ]; then
    touch /data/ssh/known_hosts
    chmod 600 /data/ssh/known_hosts
fi
exec "$@"
