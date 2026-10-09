{
    "name": "CMC - Activity-Based Costing",
    "summary": "Multistage ABC: resources -> activities -> cost objects, practical capacity, sustaining cost",
    "version": "19.0.2.0.0",
    "category": "Accounting/Management Control",
    "license": "LGPL-3",
    "author": "2ST",
    "depends": ["cmc_base", "product"],
    "data": [
        "security/ir.model.access.csv",
        "security/cmc_abc_security.xml",
        "data/cmc_abc_data.xml",
        "views/cmc_abc_views.xml",
        "report/report_actions.xml",
    ],
    "installable": True,
    "auto_install": False,
}
