from odoo.exceptions import ValidationError
from odoo.tools.safe_eval import test_python_expr


def check_expression(env, expression, label):
    """Validate a formula/ratio expression with the core safe_eval checker (AD-116)."""
    try:
        error = test_python_expr(expression or "", mode="eval")
    except NameError as exc:  # why: forbidden (dunder) names raise instead of returning the message
        error = str(exc)
    if error or not (expression or "").strip():
        raise ValidationError(env._("Invalid expression for %(label)s: %(error)s", label=label, error=error or env._("empty")))
