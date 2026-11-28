{
    'name': 'PLM Project Link',
    'version': '20.0.1.0.0',
    'summary': 'Gán tài liệu và part PLM vào 5 thư mục của dự án',
    'author': 'TDB',
    'license': 'LGPL-3',
    'depends': ['plm', 'plm_project'],
    'data': [
        'security/ir.access.csv',
        'views/assign_wizard_views.xml',
        'views/project_views.xml',
        'views/attachment_views.xml',
        'views/menu.xml',
    ],
    'installable': True,
}
