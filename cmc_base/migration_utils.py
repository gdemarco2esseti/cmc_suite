import logging

from odoo import api
from odoo.tools.translate import TranslationImporter, get_po_paths

_logger = logging.getLogger(__name__)


def reload_menu_translations(cr, module, menu_names):
    """Force the module's .po translations onto the given (renamed) menus.

    why: on upgrade Odoo rewrites only en_US of a translated name and then loads the .po files
    WITHOUT overwriting the values already in the database, so a renamed menu would keep its old
    label in every other language. Only the listed menus are touched: customised translations of
    any other term are left alone (unlike a global --i18n-overwrite).
    """
    env = api.Environment(cr, api.SUPERUSER_ID, {})
    xmlids = {f"{module}.{name}" for name in menu_names}
    importer = TranslationImporter(cr, verbose=False)
    for lang, _name in env["res.lang"].get_installed():
        if lang == "en_US":
            continue
        for po_path in get_po_paths(module, lang):
            importer.load_file(po_path, lang, xmlids=xmlids)
    importer.save(overwrite=True)
    _logger.info("%s: reloaded translations of %d renamed menu(s)", module, len(xmlids))
