{
    "name": "CMC - Time-Driven Activity-Based Costing",
    "summary": "TDABC: capacity cost rate + time equations, isolated unused capacity",
    "version": "19.0.1.0.0",
    "category": "Accounting/Management Control",
    "license": "LGPL-3",
    "author": "2ST",
    "depends": ["cmc_base", "product"],
    "data": [
        "security/ir.model.access.csv",
        "security/cmc_tdabc_security.xml",
        "views/cmc_tdabc_views.xml",
        "report/report_actions.xml",
    ],
    "installable": True,
    "auto_install": False,
}
