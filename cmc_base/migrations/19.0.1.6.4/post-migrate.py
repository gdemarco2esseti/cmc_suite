from odoo.addons.cmc_base.migration_utils import reload_menu_translations

# UX-001: menus renamed by the semantic-tabs reorganisation
RENAMED_MENUS = [
    "menu_cmc_operations",
    "menu_cmc_responsibility",
    "menu_cmc_cfg_master",
    "menu_cmc_sec_analysis",
    "menu_dash_cost_center",
]


def migrate(cr, version):
    reload_menu_translations(cr, "cmc_base", RENAMED_MENUS)
