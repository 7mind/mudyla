"""Allow running mudyla as a module: python -m mudyla."""

import sys
from .cli import main

if __name__ == "__main__":
    sys.exit(main())
