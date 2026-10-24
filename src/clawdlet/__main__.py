"""Allow running Clawdlet via ``python -m clawdlet``."""

from clawdlet.app import main

if __name__ == "__main__":
    raise SystemExit(main())
