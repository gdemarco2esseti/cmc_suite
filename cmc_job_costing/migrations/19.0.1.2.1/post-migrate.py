def migrate(cr, version):
    if not version:
        return
    # why: a forecast month has no cumulative EV/AC (NULL, not 0), so a line chart stops at the status month
    cr.execute("UPDATE cmc_evm_curve SET ev_cumulative = NULL, ac_cumulative = NULL WHERE is_forecast")
