"""Remove obsolete selection widgets before their transient fields disappear."""


def migrate(cr, version):
    cr.execute("""
        UPDATE ir_ui_view
           SET arch_db = jsonb_build_object('en_US', %s)
         WHERE id IN (
             SELECT res_id FROM ir_model_data
              WHERE module = 'tdb_custom_plm' AND name = 'selective_reject_wizard'
                AND model = 'ir.ui.view'
         )
    """, ['''<xpath expr="//form/group" position="before">
        <div class="alert alert-info mb-3" role="status" invisible="operation != 'reject'">
            Return this assembly, its reviewed child Parts, and linked files to Draft for redo.
            Released references remain unchanged.
        </div>
    </xpath>'''])
