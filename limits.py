"""Interpret only server-provided usage; never estimate a quota."""
import math


def codex_bucket(payload):
    buckets = payload.get('rateLimitsByLimitId')
    if isinstance(buckets, dict) and buckets:
        bucket = buckets.get('codex')
        return bucket if isinstance(bucket, dict) else None
    bucket = payload.get('rateLimits')
    if isinstance(bucket, dict) and bucket.get('limitId') in (None, 'codex'):
        return bucket
    return None


def remaining(window):
    if not isinstance(window, dict):
        return None
    used = window.get('usedPercent')
    if isinstance(used, bool) or not isinstance(used, (float, int)) or not math.isfinite(used) or not 0 <= used <= 100:
        return None
    return 100 - used


def window_label(window, fallback):
    minutes = window.get('windowDurationMins') if isinstance(window, dict) else None
    if minutes == 10080:
        return 'Week'
    if isinstance(minutes, (int, float)) and minutes > 0:
        return f'{minutes / 60:g}h' if minutes >= 60 else f'{minutes:g}min'
    return fallback
