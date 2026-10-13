{
    "name": "CMC - Activity-Based Costing",
    "summary": "Multistage ABC and Activity-Based Management: process view, Pareto, performance measures, initiatives, benchmark, cost of quality",
    "version": "19.0.3.0.2",
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
