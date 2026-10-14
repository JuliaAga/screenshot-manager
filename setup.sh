#!/bin/sh
set -eu
cd "$(dirname "$0")"
python3 -c 'import sys; sys.exit("Python 3.11+ is required") if sys.version_info < (3, 11) else None'
exec python3 -m app.server
