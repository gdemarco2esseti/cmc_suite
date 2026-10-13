{
    "name": "CMC - KPI and Balanced Scorecard",
    "summary": "KPI library (measures, ratios, variances, formulas, engine hooks), targets, traffic lights, trends, "
    "non-financial feeders (scrap, OTD), balanced scorecard with cascade and the monthly management pack",
    "version": "19.0.1.1.2",
    "category": "Accounting/Management Control",
    "license": "LGPL-3",
    "author": "EsseTech by DueEsseTi Srl",
    # why stock: the KPI-02 feeders (scrap, on-time delivery) read stock.scrap / stock.picking
    "depends": ["cmc_budget", "cmc_fin_analysis", "stock"],
    "data": [
        "security/cmc_kpi_security.xml",
        "security/ir.model.access.csv",
        "data/cmc_kpi_data.xml",
        "views/cmc_kpi_views.xml",
        "views/cmc_kpi_scorecard_views.xml",
        "views/cmc_kpi_run_views.xml",
        "report/cmc_kpi_pack_report.xml",
        "views/cmc_kpi_menus.xml",
    ],
    "installable": True,
    "auto_install": False,
}
