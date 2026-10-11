{
    "name": "CMC - Joint Costs",
    "summary": "Allocate joint production cost to joint products and by-products",
    "version": "19.0.1.1.1",
    "category": "Accounting/Management Control",
    "license": "LGPL-3",
    "author": "2ST",
    "depends": ["cmc_base", "product"],
    "data": [
        "security/ir.model.access.csv",
        "security/cmc_joint_security.xml",
        "views/cmc_joint_views.xml",
    ],
    "installable": True,
    "auto_install": False,
}
