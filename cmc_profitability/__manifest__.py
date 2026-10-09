{
    "name": "CMC - Profitability",
    "version": "19.0.1.0.0",
    "category": "Accounting/Accounting",
    "summary": "Customer, product and channel profitability: layered margins, whale curve, cost-to-serve, "
    "price waterfall, supplier TCO, transfer pricing, SBU margin",
    "author": "EsseTech by DueEsseTi Srl",
    "license": "LGPL-3",
    "depends": ["cmc_abc", "cmc_account", "cmc_direct_costing", "cmc_fin_analysis"],
    "data": [
        "security/cmc_profitability_security.xml",
        "security/ir.model.access.csv",
        "data/cmc_profitability_data.xml",
        "views/cmc_profit_run_views.xml",
        "views/cmc_profit_result_views.xml",
        "views/cmc_transfer_charge_views.xml",
        "views/cmc_profitability_menus.xml",
    ],
    "installable": True,
    "application": False,
    "auto_install": False,
}
