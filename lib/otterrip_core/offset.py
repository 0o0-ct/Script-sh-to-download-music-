#!/usr/bin/env python3
"""Offset / shift helpers for LRC timestamps.

Sign convention (documented here and in :func:`shift_lrc`):

    * Positive offset  → lyrics appear *later*  (add ms to each timestamp)
    * Negative offset  → lyrics appear *earlier* (subtract ms)

This is the opposite of the ``[offset:]`` LRC header convention used by
some players, which is why the offset is applied directly to timestamps
and the header is not relied upon.
"""
from __future__ import annotations

from otterrip_core.lrc_validator import shift_lrc as _shift_lrc_text


def apply_offset_to_bundle(
    lrc_text: str,
    ms_offset: int,
) -> str:
    """Shift every timestamp in ``lrc_text`` by ``ms_offset`` ms.

    Returns a normalised LRC string.  Wraps :func:`shift_lrc` for
    clarity; see that function for sign convention.
    """
    return _shift_lrc_text(lrc_text, ms_offset)


def compute_offset_from_anchor(
    current_ts_ms: int,
    anchor_ts_ms: int,
) -> int:
    """Return the ms needed to move ``current_ts_ms`` to ``anchor_ts_ms``.

    ``offset = anchor - current``.  Positive = shift later.
    """
    return anchor_ts_ms - current_ts_ms