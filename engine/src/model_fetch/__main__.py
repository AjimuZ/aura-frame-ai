"""Entry point of the fetch-models image: `python -m model_fetch [model ...|all] [--list]`."""

import sys

from .fetch import main

sys.exit(main())
