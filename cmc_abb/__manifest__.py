{
    "name": "CMC - Activity-Based Budgeting and Capacity",
    "summary": "Reverse ABC from forecast volume, spending profiles, capacity time states, driver what-if, ABC variances",
    "version": "19.0.1.0.3",
    "category": "Accounting/Management Control",
    "license": "LGPL-3",
    "author": "EsseTech by DueEsseTi Srl",
    "depends": ["cmc_abc", "cmc_budget"],
    "data": [
        "security/ir.model.access.csv",
        "security/cmc_abb_security.xml",
        "views/cmc_abb_views.xml",
        "views/cmc_abb_menus.xml",
    ],
    "installable": True,
    "application": False,
    "auto_install": False,
}
