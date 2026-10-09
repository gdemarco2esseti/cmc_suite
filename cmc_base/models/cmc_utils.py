import calendar
import math
import statistics
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


# ---- sampling and rank statistics (stdlib only, AD-119) ----------------------

_DISTRIBUTION_KEYS = {"normal": ("mean", "stdev"), "triangular": ("low", "mode", "high"), "uniform": ("low", "high")}


def _check_distribution(distribution, params):
    keys = _DISTRIBUTION_KEYS.get(distribution)
    if keys is None:
        raise ValueError("Unknown distribution %s." % distribution)
    p = [params[key] for key in keys]
    if distribution == "normal" and p[1] < 0:
        raise ValueError("The standard deviation cannot be negative.")
    if distribution == "triangular" and not (p[0] <= p[1] <= p[2] and p[0] < p[2]):
        raise ValueError("A triangular distribution needs low <= mode <= high and low < high.")
    if distribution == "uniform" and p[0] >= p[1]:
        raise ValueError("A uniform distribution needs low < high.")
    return p


def inverse_cdf(distribution, params, p):
    values = _check_distribution(distribution, params)
    if not 0.0 <= p <= 1.0:
        raise ValueError("A probability lies in [0, 1].")
    if distribution == "normal":
        mean, stdev = values
        # why: inv_cdf is undefined at 0 and 1 and for a zero deviation (a constant)
        return mean if not stdev else statistics.NormalDist(mean, stdev).inv_cdf(min(max(p, 1e-12), 1 - 1e-12))
    if distribution == "uniform":
        return values[0] + p * (values[1] - values[0])
    low, mode, high = values
    if p < (mode - low) / (high - low):
        return low + math.sqrt(p * (high - low) * (mode - low))
    return high - math.sqrt((1 - p) * (high - low) * (high - mode))


def draw(distribution, params, n, rng, lhs=False):
    _check_distribution(distribution, params)
    if n < 1:
        raise ValueError("At least one sample is needed.")
    if lhs:
        # one sample in each of the n equal-probability strata, in shuffled order
        probabilities = [(i + rng.random()) / n for i in range(n)]
        rng.shuffle(probabilities)
    else:
        probabilities = [rng.random() for _i in range(n)]
    return [inverse_cdf(distribution, params, p) for p in probabilities]


def percentile(values, q):
    if not values:
        raise ValueError("No values.")
    if not 0 <= q <= 100:
        raise ValueError("A percentile lies in [0, 100].")
    ordered = sorted(values)
    position = (len(ordered) - 1) * q / 100
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def ranks(values):
    order = sorted(range(len(values)), key=values.__getitem__)
    out = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        for k in range(i, j + 1):
            out[order[k]] = (i + j) / 2 + 1.0
        i = j + 1
    return out


def spearman(xs, ys):
    if len(xs) != len(ys) or len(xs) < 2:
        raise ValueError("Two series of the same length, at least 2 points.")
    rx, ry = ranks(list(xs)), ranks(list(ys))
    mx, my = statistics.fmean(rx), statistics.fmean(ry)
    sxx = sum((a - mx) ** 2 for a in rx)
    syy = sum((b - my) ** 2 for b in ry)
    if not sxx or not syy:
        return 0.0
    sxy = sum((a - mx) * (b - my) for a, b in zip(rx, ry, strict=True))
    return max(-1.0, min(1.0, sxy / math.sqrt(sxx * syy)))


def histogram(values, bins):
    if bins < 1 or not values:
        raise ValueError("At least one bin and one value.")
    low, high = min(values), max(values)
    if low == high:
        return [(low, high, len(values))]
    width = (high - low) / bins
    counts = [0] * bins
    for value in values:
        counts[min(int((value - low) / width), bins - 1)] += 1
    return [(low + i * width, high if i == bins - 1 else low + (i + 1) * width, count) for i, count in enumerate(counts)]


def describe(values):
    return {
        "mean": statistics.fmean(values),
        "stdev": statistics.stdev(values) if len(values) > 1 else 0.0,
        "min": min(values),
        "max": max(values),
        "p5": percentile(values, 5),
        "p50": percentile(values, 50),
        "p95": percentile(values, 95),
        "prob_positive": sum(1 for value in values if value > 0) / len(values),
    }
