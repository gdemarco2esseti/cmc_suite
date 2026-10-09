def unfreeze_record_rules(cr, module):
    # why: the record rules moved out of noupdate="1" in the XML, but Odoo never
    # clears the flag of an existing xmlid, so on installed databases they would
    # stay frozen and a domain fix would never reach them
    cr.execute(
        "UPDATE ir_model_data SET noupdate = false WHERE module = %s AND model = 'ir.rule' AND noupdate",
        (module,),
    )


def migrate(cr, version):
    unfreeze_record_rules(cr, 'cmc_full_costing')
