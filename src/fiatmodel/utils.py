"""Utility functions for Fiat Model."""
from typing import List
import pandas as pd

# "private" global helper functions
def _union_sorted_times(all_times: List[pd.DatetimeIndex]) -> pd.DatetimeIndex:
    if not all_times:
        return pd.DatetimeIndex([])
    out = all_times[0]
    for t in all_times[1:]:
        out = out.union(t)
    return out.sort_values()
