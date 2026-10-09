#!/bin/sh
# eigenaugen.sh -- launcher for the eigenaugen Python package.
#
# ./scripts/install.sh puts an `eigenaugen` command on PATH that runs this
# file. All arguments pass through: eigenaugen review -h prints the options.
set -eu

# shellcheck source=SCRIPTDIR/lib/common.sh
. "$(dirname "$0")/lib/common.sh"

VENV_PYTHON="$PROJECT_ROOT/.venv/bin/python"
PACKAGE="eigenaugen.src"

[ -x "$VENV_PYTHON" ] || die ".venv missing -- $BOOTSTRAP_HINT"

# -P keeps the current directory (possibly an untrusted checkout) off
# sys.path; PYTHONPATH supplies the package instead.
export PYTHONPATH="$PROJECT_ROOT"
exec "$VENV_PYTHON" -P -m "$PACKAGE" "$@"
