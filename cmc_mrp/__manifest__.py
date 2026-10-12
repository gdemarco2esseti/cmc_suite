{
    "name": "CMC - MRP Bridge (Prime Cost)",
    "summary": "Feed prime cost (materials MP + manufacturing labor MOD) from MRP "
               "into management control",
    "version": "19.0.1.4.2",
    "category": "Accounting/Management Control",
    "license": "LGPL-3",
    "author": "2ST",
    "depends": ["cmc_account", "mrp_account"],
    "data": [
        "data/cmc_measure_data.xml",
        "views/mrp_production_views.xml",
    ],
    "installable": True,
    "auto_install": False,
}
