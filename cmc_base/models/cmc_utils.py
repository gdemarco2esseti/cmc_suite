import calendar
import math
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


def equivalent_units(method, completed, ending_units, ending_pct, beginning_units=0.0, beginning_pct=0.0, spoiled_units=0.0, spoiled_pct=100.0):
    """Equivalent units of ONE cost element (percentages 0..100). Weighted average merges the beginning WIP;
    FIFO subtracts the work it already carries (V = P + alpha Rsf - beta Rsi)."""
    if method not in ("weighted_average", "fifo"):
        raise ValueError("unknown method %s" % method)
    units = completed + ending_units * ending_pct / 100.0 + spoiled_units * spoiled_pct / 100.0
    return units - beginning_units * beginning_pct / 100.0 if method == "fifo" else units


def spoilage_split(spoiled_units, good_units, normal_pct):
    """(normal, abnormal) spoiled units: normal up to `normal_pct` % of the good units."""
    normal = min(spoiled_units, good_units * normal_pct / 100.0)
    return normal, spoiled_units - normal


def process_cost_report(method, units, elements, rounding=None):
    """Cost of production report of one department: equivalent units, cost per EU (full precision) and
    assignment per cost element. The spoiled units are inspected at the end of the process (100 %)."""
    spoiled = units["normal_spoiled"] + units["abnormal_spoiled"]
    accounted = units["completed"] + units["ending"] + spoiled
    if abs(units["beginning"] + units["started"] - accounted) > 1e-6:
        raise ValueError("units to account for differ from units accounted for")
    result = {"elements": {}, "cost_to_account": 0.0, "transferred": 0.0, "ending": 0.0, "abnormal": 0.0, "normal_spoilage": 0.0}
    for key, element in elements.items():
        eu = equivalent_units(
            method, units["completed"], units["ending"], element["ending_pct"], units["beginning"], element["beginning_pct"], spoiled
        )
        to_account = element["beginning_cost"] + element["period_cost"]
        basis = to_account if method == "weighted_average" else element["period_cost"]
        if abs(eu) < 1e-9:
            if abs(basis) > 1e-9:
                raise ValueError("element %s has cost but no equivalent units" % key)
            cost_per_eu = 0.0
        else:
            cost_per_eu = basis / eu
        ending = units["ending"] * element["ending_pct"] / 100.0 * cost_per_eu
        abnormal = units["abnormal_spoiled"] * cost_per_eu
        normal = units["normal_spoiled"] * cost_per_eu
        parts = {"transferred": to_account - ending - abnormal, "ending": ending, "abnormal": abnormal}
        if rounding:
            to_account = float_round(to_account, precision_rounding=rounding)
            parts = largest_remainder(to_account, parts, rounding)
            normal = float_round(normal, precision_rounding=rounding)
        result["elements"][key] = {"eu": eu, "cost_per_eu": cost_per_eu, "cost_to_account": to_account, "normal_spoilage": normal, **parts}
        for name in ("cost_to_account", "normal_spoilage", *parts):
            result[name] += result["elements"][key][name]
    return result


def simple_payback(one_time_cost, annual_saving):
    """Years to recover the investment; 0.0 when there is no saving to recover it with."""
    return one_time_cost / annual_saving if annual_saving > 0 else 0.0


def _check_three_points(o, m, p):
    if not o <= m <= p:
        raise ValueError("Three-point estimate needs optimistic <= most likely <= pessimistic")


def pert_estimate(o, m, p):
    """(expected, sigma) of the PERT beta approximation."""
    _check_three_points(o, m, p)
    return (o + 4 * m + p) / 6.0, (p - o) / 6.0


def triangular_estimate(o, m, p):
    _check_three_points(o, m, p)
    return (o + m + p) / 3.0


def pert_total(estimates, z=2.0):
    """(expected, sigma, low, high) of independent (o, m, p) estimates: sigma = root sum of squares."""
    pairs = [pert_estimate(*e) for e in estimates]
    expected = sum(e for e, _s in pairs)
    sigma = math.sqrt(sum(s * s for _e, s in pairs))
    return expected, sigma, expected - z * sigma, expected + z * sigma


def emv(probability, impact):
    """Expected monetary value; `probability` is a fraction, a negative impact is an opportunity."""
    if not 0 <= probability <= 1:
        raise ValueError("A probability lies between 0 and 1")
    return probability * impact


def contingency_reserve(risks):
    return sum(emv(probability, impact) for probability, impact in risks)


def evm_indices(bac, pv, ev, ac):
    """Earned value indices and EAC variants; an undefined ratio, and everything built on it, is None."""
    cpi = ev / ac if ac else None
    spi = ev / pv if pv else None
    eac = bac / cpi if cpi else None
    combined = cpi * spi if cpi and spi else None
    return {
        "cv": ev - ac,
        "sv": ev - pv,
        "cpi": cpi,
        "spi": spi,
        "eac": eac,
        "eac_atypical": ac + (bac - ev),
        "eac_composite": ac + (bac - ev) / combined if combined else None,
        "etc": eac - ac if eac is not None else None,
        "vac": bac - eac if eac is not None else None,
        "tcpi": (bac - ev) / (bac - ac) if bac != ac else None,
    }


def control_breaches(values, lower, upper):
    """Indexes of the values strictly outside [lower, upper]."""
    return [i for i, value in enumerate(values) if value < lower or value > upper]


def rule_of_seven(values, center=0.0, run=7):
    """True when `run` consecutive values sit strictly on the same side of `center`."""
    streak, side = 0, 0
    for value in values:
        current = (value > center) - (value < center)
        streak = streak + 1 if current and current == side else (1 if current else 0)
        side = current
        if streak >= run:
            return True
    return False
