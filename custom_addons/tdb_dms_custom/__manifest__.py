# -*- coding: utf-8 -*-
{
    'name': 'TDB DMS Customization - Subfolders & Recycle Bin',
    'version': '1.0',
    'category': 'Document Management',
    'summary': 'Tạo thư mục con trong phòng ban & Thùng rác khôi phục file dành cho Admin',
    'description': """
Module tùy biến DMS cho TDB Solution:

* Chức năng tạo thư mục con chứa file trong phòng ban (có quyền bật/tắt tải về).
* Phòng ban Thùng Rác (Recycle Bin) bảo mật: Chỉ Sếp/Admin mới có quyền truy cập.
* Khi xóa file, file được tự động chuyển vào Thùng Rác (kèm tên người xóa, thời gian xóa, thư mục gốc).
* Sếp/Admin có quyền Khôi phục (Restore) về thư mục ban đầu hoặc Xóa vĩnh viễn.
""",
    'author': 'TDB Solution',
    'depends': ['dms', 'base', 'web', 'mail'],
    'data': [
        'security/ir.access.csv',
        'data/recycle_bin_data.xml',
        'wizard/create_subfolder_wizard_views.xml',
        'wizard/file_date_filter_wizard_views.xml',
        'views/dms_directory_views.xml',
        'views/dms_file_views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'tdb_dms_custom/static/src/scss/dms_preview.scss',
        ],
    },
    'installable': True,
    'application': False,
    'auto_install': False,
    'license': 'LGPL-3',
}
