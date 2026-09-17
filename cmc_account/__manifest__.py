{
    "name": "CMC - Accounting Bridge",
    "version": "19.0.1.0.0",
    "category": "Accounting/Accounting",
    "summary": "Feed accounting analytic lines into cost management with auto-classification",
    "author": "2ST",
    "license": "LGPL-3",
    "depends": ["cmc_base", "account"],
    "data": [
        "security/ir.model.access.csv",
        "security/cmc_account_security.xml",
        "views/cmc_classification_rule_views.xml",
        "views/account_account_views.xml",
        "views/cmc_exception_views.xml",
    ],
    "installable": True,
    "application": False,
    "auto_install": False,
}
