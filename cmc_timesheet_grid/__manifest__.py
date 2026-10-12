{
    "name": "CMC - Timesheet Grid Bridge",
    "summary": "Let CMC controllers classify validated timesheets",
    "version": "19.0.1.0.0",
    "category": "Accounting/Management Control",
    # why OPL-1: third-party module that only runs on top of Enterprise (timesheet_grid)
    "license": "OPL-1",
    "author": "2ST",
    "depends": ["timesheet_grid", "cmc_hr_timesheet"],
    "installable": True,
    "auto_install": True,
}
