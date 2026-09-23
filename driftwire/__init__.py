"""DriftWire — API contract drift detection.

Two capabilities, one command line:

  driftwire check <spec> --url <base-url>   # spec vs reality (live HTTP)
  driftwire diff  <old> <new>               # spec vs spec (breaking changes)

Pro (licensed): HTML drift reports (`--format html`) and `.driftwire.yml`
waiver/exception config (`--config`).
"""

__version__ = "0.2.1"
