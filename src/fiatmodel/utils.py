"""Utility functions for Fiat Model."""
# built-in imports
from typing import List

# external
import pandas as pd

# "private" global helper functions
def union_sorted_times(all_times: List[pd.DatetimeIndex]) -> pd.DatetimeIndex:
    """Return the sorted union of multiple DateTime indices.

    Parameters
    ----------
    all_times : list of pandas.DatetimeIndex
        Collection of ``DatetimeIndex`` objects to merge. If the list is
        empty, an empty ``DatetimeIndex`` is returned.

    Returns
    -------
    pandas.DatetimeIndex
        Sorted union of all input indices with duplicates removed.

    Notes
    -----
    - Operates with set-union semantics; duplicate timestamps are removed.
    - The resulting index does not guarantee a fixed frequency (`freq=None`).
    - For best results, ensure all input indices share the same timezone
      awareness to avoid pandas warnings.

    Examples
    --------
    >>> import pandas as pd
    >>> a = pd.DatetimeIndex(['2020-01-01', '2020-01-03'])
    >>> b = pd.DatetimeIndex(['2020-01-02'])
    >>> union_sorted_times([a, b])
    DatetimeIndex(['2020-01-01', '2020-01-02', '2020-01-03'], dtype='datetime64[ns]', freq=None)
    """
    if not all_times:
        return pd.DatetimeIndex([])
    out = all_times[0]
    for t in all_times[1:]:
        out = out.union(t)
    return out.sort_values()
