#!/bin/sh
set -eu

cat >/usr/share/nginx/html/runtime-config.js <<EOF
window.__CONCORD_CONFIG__ = {
  apiUrl: "${PYTHON_API_URL:-/api/python}"
};
EOF

exec nginx -g "daemon off;"
