# Real-source corpus

Unmodified third-party GPU sources, used to measure how much real code qpel can
import. Each entry in `corpus.json` records where the file came from, its licence
and its SHA-256; the licence files sit next to the sources. `include/` has
declaration-only header stubs so clang can parse the files offline.

    python3 -m frontends.corpus             # coverage report (run in portable/)
    python3 -m frontends.corpus --details   # every blocker with its location
    python3 -m frontends.corpus --update    # rewrite coverage.json

`coverage.json` is the recorded result; `tests/test_corpus.py` fails when it
changes. See IMPORT.md, "Real-source corpus and coverage".
