"""Unified official batch history entry point for the original report pipeline."""
from fetch_tw_bulk import get_bulk_history


def get_tw_history(tw_universe, as_of=None, persist=True):
    return get_bulk_history(tw_universe, as_of=as_of, persist=persist)
