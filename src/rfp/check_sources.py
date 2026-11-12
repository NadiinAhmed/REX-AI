"""Quick live check of the real tender sources (needs internet, no API key, no database).

    python -m src.rfp.check_sources            # all live sources
    python -m src.rfp.check_sources etimad     # one source: worldbank | etimad | ungm

For each source it prints how many listings were read, how many are in Egypt / Saudi Arabia and
look training-related, and a few example titles. Nothing is stored.
"""
from __future__ import annotations

import sys

from src.rfp.etimad import EtimadSource
from src.rfp.extraction import extract
from src.rfp.models import Country
from src.rfp.sources import SourceError
from src.rfp.ungm import UngmSource
from src.rfp.worldbank import WorldBankSource

SOURCES = {"worldbank": WorldBankSource, "etimad": EtimadSource, "ungm": UngmSource}


def check(name: str) -> None:
    source = SOURCES[name]()
    print(f"\n=== {source.name}")
    try:
        tenders = source.fetch()
    except SourceError as exc:
        print(f"  FAILED: {exc}")
        return
    except Exception as exc:                       # show anything unexpected instead of a traceback wall
        print(f"  FAILED: {type(exc).__name__}: {exc}")
        return
    print(f"  {source.info.notice}")
    by_country = {Country.EGYPT: 0, Country.SAUDI_ARABIA: 0}
    training = []
    for t in tenders:
        opp, is_training = extract(t)
        if opp.country in by_country:
            by_country[opp.country] += 1
            if is_training:
                training.append((opp.country.label, opp.deadline, opp.title))
    print(f"  read {len(tenders)} · Egypt {by_country[Country.EGYPT]} · Saudi Arabia {by_country[Country.SAUDI_ARABIA]}"
          f" · training-related {len(training)}")
    for country, deadline, title in training[:8]:
        print(f"   - [{country}] {deadline or 'no deadline'} · {title[:110]}")


def main(argv: list[str]) -> int:
    names = argv or list(SOURCES)
    unknown = [n for n in names if n not in SOURCES]
    if unknown:
        print(f"Unknown source(s): {', '.join(unknown)}. Use: {', '.join(SOURCES)}")
        return 2
    for name in names:
        check(name)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
