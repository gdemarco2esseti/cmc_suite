{
    "name": "CMC - MRP Bridge (Prime Cost)",
    "summary": "Feed prime cost (materials MP + manufacturing labor MOD) from MRP "
               "into management control",
    "version": "19.0.1.0.0",
    "category": "Accounting/Management Control",
    "license": "LGPL-3",
    "author": "2ST",
    "depends": ["cmc_account", "mrp_account"],
    "data": [
        "views/mrp_production_views.xml",
    ],
    "installable": True,
    "auto_install": False,
}
