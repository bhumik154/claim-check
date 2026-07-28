"""Shared argparse helpers for the three entry points.

Kept out of runner.py so the runner stays free of CLI concerns, and out of
each entry point so the three don't drift apart.
"""

import argparse


def positive_timeout(raw: str) -> float:
    """argparse `type=` callable for --timeout.

    Rejects zero and negative values deliberately, breaking this project's
    otherwise-universal fail-open rule. A non-positive timeout kills every
    run the instant it starts and then fails open, so the tool reports
    "could not verify ... allowing commit" forever while looking like it is
    working - silently verifying nothing is a worse outcome than a loud
    error about a flag the user typed wrong.
    """
    try:
        value = float(raw)
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError(f"invalid float value: {raw!r}")
    if value <= 0:
        raise argparse.ArgumentTypeError(
            f"--timeout must be greater than 0 (got {value}); a non-positive timeout "
            "kills every run instantly and silently verifies nothing"
        )
    return value
