{
    "name": "CMC - Standard Cost",
    "version": "19.0.1.1.2",
    "category": "Accounting/Accounting",
    "summary": "Standard cost cards, predetermined rates, MO variances, over/under-absorbed overhead, standard price push",
    "author": "EsseTech by DueEsseTi Srl",
    "license": "LGPL-3",
    "depends": ["cmc_budget", "cmc_mrp"],
    "data": [
        "security/ir.model.access.csv",
        "security/cmc_standard_security.xml",
        "views/cmc_standard_views.xml",
    ],
    "installable": True,
    "application": False,
    "auto_install": False,
}
