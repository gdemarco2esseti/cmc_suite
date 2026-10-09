import calendar
from datetime import timedelta

from odoo.tools import float_round


def largest_remainder(total, raw, rounding):
    """Round the values of `raw` ({key: value}) so the rounded parts re-sum to
    `total` exactly at the given `rounding`, pushing the residual to the keys
    with the largest fractional remainder. Conserves the total (no cents lost)."""
    rounded = {k: float_round(v, precision_rounding=rounding) for k, v in raw.items()}
    residual = float_round(total - sum(rounded.values()), precision_rounding=rounding)
    if residual and rounded:
        step = rounding if residual > 0 else -rounding
        order = sorted(raw, key=lambda k: raw[k] - rounded[k], reverse=residual > 0)
        max_iter = len(order) * (int(abs(residual) / rounding) + 2) + 1
        i = 0
        while float_round(residual, precision_rounding=rounding) != 0:
            if i > max_iter:
                raise ValueError("largest_remainder did not converge")
            k = order[i % len(order)]
            rounded[k] = float_round(rounded[k] + step, precision_rounding=rounding)
            residual = float_round(residual - step, precision_rounding=rounding)
            i += 1
    return rounded


def qty_rounding(env):
    """Rounding step of a driver or product quantity (a quantity, never a currency)."""
    return 10 ** -env["decimal.precision"].precision_get("Product Unit")


def spread(total, weights, rounding):
    """Split `total` across `weights` ({key: weight}) proportionally, rounded
    with largest_remainder so the parts re-sum exactly to `total` rounded at
    `rounding`. The caller has already rejected a zero weight base."""
    coeff = total / sum(weights.values())
    raw = {key: coeff * weight for key, weight in weights.items()}
    return largest_remainder(float_round(total, precision_rounding=rounding), raw, rounding)


def month_start(day):
    return day.replace(day=1)


def month_end(day):
    return day.replace(day=calendar.monthrange(day.year, day.month)[1])


def month_range(date_from, date_to):
    """First day of every calendar month touched by the span, both ends inclusive."""
    if date_to < date_from:
        raise ValueError("date_to is before date_from")
    months = []
    month = month_start(date_from)
    while month <= date_to:
        months.append(month)
        month = month_start(month_end(month) + timedelta(days=1))
    return months


def phase_by_month(total, date_from, date_to, rounding, weights=None):
    """Split `total` over the months of the span (`spread`, so the parts re-sum to
    `total`). `weights` = {calendar month number: weight}, a missing month weighs 0;
    None = one equal weight per month."""
    months = month_range(date_from, date_to)
    month_weights = {m: 1 if weights is None else weights.get(m.month, 0) for m in months}
    if not sum(month_weights.values()):
        raise ValueError("the weights of the span sum to zero")
    return spread(total, month_weights, rounding)


def high_low(points):
    """(K, v) of C = K + v*Q through the points with the lowest and highest Q."""
    points = list(points)
    if len(points) < 2:
        raise ValueError("at least two points are needed")
    low = min(points, key=lambda p: p[0])
    high = max(points, key=lambda p: p[0])
    if high[0] == low[0]:
        raise ValueError("the quantities are all equal")
    v = (high[1] - low[1]) / (high[0] - low[0])
    return low[1] - v * low[0], v


def ols_fit(points):
    """(K, v, r2) of the least squares line C = K + v*Q; r2 is 1.0 on a flat cost."""
    points = list(points)
    if len(points) < 2:
        raise ValueError("at least two points are needed")
    n = len(points)
    mean_q = sum(q for q, _c in points) / n
    mean_c = sum(c for _q, c in points) / n
    sxx = sum((q - mean_q) ** 2 for q, _c in points)
    if not sxx:
        raise ValueError("the quantities are all equal")
    sxy = sum((q - mean_q) * (c - mean_c) for q, c in points)
    syy = sum((c - mean_c) ** 2 for _q, c in points)
    v = sxy / sxx
    return mean_c - v * mean_q, v, sxy**2 / (sxx * syy) if syy else 1.0
