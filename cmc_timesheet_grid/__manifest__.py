{
    "name": "CMC - Timesheet Grid Bridge",
    "summary": "Let CMC controllers classify validated timesheets",
    "version": "19.0.1.0.0",
    "category": "Accounting/Management Control",
    # why OEEL-1: it depends on an Enterprise module (timesheet_grid)
    "license": "OEEL-1",
    "author": "2ST",
    "depends": ["timesheet_grid", "cmc_hr_timesheet"],
    "installable": True,
    "auto_install": True,
}
