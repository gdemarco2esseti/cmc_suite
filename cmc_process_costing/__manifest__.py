{
    "name": "CMC - Process Costing",
    "version": "19.0.1.0.2",
    "category": "Accounting/Management Control",
    "summary": "Process costing per department and month: equivalent units (weighted average / FIFO), "
               "spoilage, transferred-in chaining, cost of production report",
    "author": "EsseTech by DueEsseTi Srl",
    "license": "LGPL-3",
    "depends": ["cmc_mrp"],
    "data": [
        "security/ir.model.access.csv",
        "security/cmc_process_costing_security.xml",
        "views/cmc_process_run_views.xml",
        "views/cmc_process_costing_menus.xml",
        "report/report_cost_of_production.xml",
    ],
    "installable": True,
    "application": False,
    "auto_install": False,
}
