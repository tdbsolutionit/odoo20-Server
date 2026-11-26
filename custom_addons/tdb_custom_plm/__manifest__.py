{
    'name': 'TDB Custom PLM',
    'version': '20.0.4.0.5',
    'summary': 'Omnia CAD, live PLM views and level-based review / redo activities',
    'author': 'TDB',
    'category': 'Manufacturing/Product Lifecycle Management (PLM)',
    'depends': ['rpc', 'plm', 'bus', 'web', 'activity_validation'],
    'assets': {
        'web.assets_backend': [
            'tdb_custom_plm/static/src/live_refresh_queue.js',
            'tdb_custom_plm/static/src/plm_live_refresh.js',
            'tdb_custom_plm/static/src/plm_activity_menu.js',
            'tdb_custom_plm/static/src/plm_chatter_target.js',
            'tdb_custom_plm/static/src/plm_chatter.js',
        ],
    },
    'data': [
        'security/review_security.xml',
        'data/workflow_activity_types.xml',
        'views/ir_attachment_views.xml',
        'views/review_workflow_views.xml',
        'views/workflow_activity_views.xml',
        'views/review_recovery_views.xml',
        'views/selective_review_views.xml',
    ],
    'installable': True,
    'application': False,
    'license': 'LGPL-3',
}
