{
    "name": "CMC - Activity-Based Costing",
    "summary": "Two-stage ABC: resource costs -> activities -> cost objects",
    "version": "19.0.1.0.0",
    "category": "Accounting/Management Control",
    "license": "LGPL-3",
    "author": "2ST",
    "depends": ["cmc_base", "product"],
    "data": [
        "security/ir.model.access.csv",
        "security/cmc_abc_security.xml",
        "views/cmc_abc_views.xml",
    ],
    "installable": True,
    "auto_install": False,
}
