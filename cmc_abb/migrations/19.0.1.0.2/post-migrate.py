from odoo.addons.cmc_base.migration_utils import reload_menu_translations

# UX-001: menus renamed by the semantic-tabs reorganisation
RENAMED_MENUS = [
    "menu_cmc_abb_variance_run",
    "menu_cmc_abb_variance_line",
    "menu_cmc_abb_whatif",
]


def migrate(cr, version):
    reload_menu_translations(cr, "cmc_abb", RENAMED_MENUS)
