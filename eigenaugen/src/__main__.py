"""Entry point: python -P -m eigenaugen.src COMMAND [ARGS]."""

import sys

from eigenaugen.src.cli import main

if __name__ == "__main__":
    sys.exit(main())
