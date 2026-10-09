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


def npv(rate, flows):
    """Net present value of `flows` (index = period, flows[0] undiscounted); `rate` is a fraction."""
    return sum(flow / (1.0 + rate) ** period for period, flow in enumerate(flows))


class MultipleIrrError(ValueError):
    """More than one sign change: several IRRs may exist and none is meaningful."""


def irr(flows, precision=1e-9):
    """Rate (fraction) with npv = 0, by bisection. ValueError when there is none to find."""
    if not (any(f > 0 for f in flows) and any(f < 0 for f in flows)):
        raise ValueError("The flows do not change sign: no IRR")
    signs = [f > 0 for f in flows if f]
    if sum(a != b for a, b in zip(signs, signs[1:], strict=False)) > 1:
        raise MultipleIrrError("The flows change sign more than once: multiple IRR")
    low, high = -0.99, 1.0
    npv_low = npv(low, flows)
    while npv_low * npv(high, flows) > 0:
        high *= 2
        if high > 1e6:
            raise ValueError("No IRR found")
    for _i in range(200):
        mid = (low + high) / 2
        npv_mid = npv(mid, flows)
        if npv_low * npv_mid > 0:
            low, npv_low = mid, npv_mid
        else:
            high = mid
        if high - low < precision:
            break
    return (low + high) / 2


def payback(flows):
    """Periods (linear inside the period) at which the cumulative flow reaches 0, None if it never does."""
    if not flows or flows[0] >= 0:  # no investment at t = 0: nothing to pay back
        return None
    cumulative = 0.0
    for period, flow in enumerate(flows):
        previous, cumulative = cumulative, cumulative + flow
        if cumulative >= 0:
            return period - 1 + (-previous / flow) if previous < 0 else float(period)
    return None


def discounted_payback(rate, flows):
    return payback([flow / (1.0 + rate) ** period for period, flow in enumerate(flows)])


def nominal_rate(real, inflation):
    return (1.0 + real) * (1.0 + inflation) - 1.0


def real_rate(nominal, inflation):
    return (1.0 + nominal) / (1.0 + inflation) - 1.0


def indifference_qty(fixed_a, var_a, fixed_b, var_b):
    """Q where fixed_a + var_a * Q = fixed_b + var_b * Q; None for parallel structures."""
    if var_a == var_b:
        return None
    return (fixed_b - fixed_a) / (var_a - var_b)
