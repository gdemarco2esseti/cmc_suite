{
    "name": "CMC - KPI MRP Bridge",
    "summary": "Manufacturing process KPIs from MRP: cycle time, manufacturing lead time, PPM, first-pass yield, "
    "manufacturing cycle efficiency and delivery lead time (KPI-02 feeders)",
    "version": "19.0.1.0.0",
    "category": "Accounting/Management Control",
    "license": "LGPL-3",
    "author": "EsseTech by DueEsseTi Srl",
    # why both: the feeders read mrp.workorder / mrp.production (cmc_mrp) into the cmc_kpi measure catalogue
    "depends": ["cmc_kpi", "cmc_mrp"],
    "data": [],
    "installable": True,
    "auto_install": True,
}
