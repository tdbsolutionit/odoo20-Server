# -*- coding: utf-8 -*-
# Copyright 2026 TDB Solution Co., Ltd.
{
    "name": "TDB PLM Customization - Giao diện & Access Token 180 Ngày SolidWorks",
    "version": "20.0.1.9.0",
    "category": "Manufacturing/Product Lifecycle Management (PLM)",
    "summary": "Tùy biến chuẩn hóa PLM, Document Lock Key Holder, File History, Assembly References, Version Restore, 2D Drawing-3D Model (LyTree) và Access Token 180 ngày SolidWorks",
    "description": """
TDB Solution - Module tùy biến phân hệ PLM (Product Lifecycle Management)
========================================================================
Tính năng chính:
1. Chuẩn hóa 100% giao diện tiếng Việt trên danh sách linh kiện kỹ thuật (Engineering Parts), tài liệu kỹ thuật (Documents) và hệ thống Menu.
2. Cơ chế khóa tài liệu theo Kiến trúc Người giữ Key (Key Holder Architecture):
   - Kỹ sư chủ động nhận Key bằng nút 'Lock' trên giao diện.
   - Mọi kỹ sư đều được quyền Check-out tải file về máy trạm phục vụ thiết kế, tham chiếu.
   - Duy nhất kỹ sư giữ Key mới được quyền Check-in cập nhật file lên máy chủ (áp dụng cho cả Admin).
   - Quản trị viên có nút 'Force Unlock (Thu hồi Key)', kỹ sư có nút 'Unlock' để chuyển giao / nhả Key.
3. Lịch sử phiên bản tệp (Task T4 File History): Tự động lưu snapshot phiên bản cũ (v1, v2,...) khi Check-in.
4. Tự động cấp Access Token & Session Cookie 180 ngày cho Add-in CAD SolidWorks 2022.
5. Quản lý file tham chiếu Assembly & Where-Used (Task 5):
   - Chuẩn hóa quan hệ ir.attachment.relation trên ir.attachment (child_relation_ids & parent_relation_ids).
   - Core API helpers: get_child_documents, get_where_used_documents, add_child_reference.
   - Tab trực quan trên Form view Document hiển thị danh sách chi tiết con và nơi sử dụng với Preview, Rev, State, Lock State.
6. Khôi phục phiên bản (Version Restore - Append-Only Rollback):
   - Cơ chế bảo vệ 2 lớp: Quyền giữ Key cục bộ và bảo vệ Cụm máy cha (Where-Used Protection).
   - Khôi phục an toàn: Tự động sinh phiên bản tiếp theo mang nội dung snapshot cũ mà không mất dữ liệu lịch sử.
   - Tự động ghi nhận nhật ký kiểm toán (Audit Trail) vào Chatter và thông báo Client Action.
7. Liên kết Drawing với Part/Assembly (Task HT-1 Quan hệ tài liệu 2D - 3D LyTree):
   - Chuẩn hóa quan hệ 2D-3D thông qua ir.attachment.relation (link_kind='LyTree').
   - Smart buttons trên Form view: 'Bản vẽ 2D' (đếm và mở 2D từ 3D) và 'Mô hình 3D' (đếm và mở 3D từ 2D).
   - Helper methods: get_related_drawings(), get_related_3d_models(), link_drawing_document(), action_view_drawings(), action_open_source_3d().
   - Tab References chuyên biệt hiển thị bản vẽ 2D (.slddrw), trạng thái printout_name và nút icon xem/tải PDF kỹ thuật trực tiếp (/plm/ir_attachment_printout/<id>).
   - Tương thích 100% với hàm getRelatedLyTree của Omnia PLM và SolidWorks Add-in.
    """,
    "author": "TDB Solution",
    "website": "https://tdbsolution.com",
    "license": "LGPL-3",
    "depends": [
        "plm",
        "product",
        "mrp",
        "bus",
    ],
    "data": [
        "security/plm_desktop_token_security.xml",
        "security/ir.access.csv",
        "wizard/plm_transfer_key_wizard_views.xml",
        "wizard/plm_drawing_link_wizard_views.xml",
        "wizard/plm_bom_diff_wizard_views.xml",
        "views/plm_component_views.xml",
        "views/plm_document_views.xml",
        "views/plm_desktop_token_views.xml",
        "views/plm_menu_views.xml",
        "views/mrp_bom_views.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "tdb_plm_custom/static/src/scss/plm_variables.scss",
            "tdb_plm_custom/static/src/scss/plm_table_layout.scss",
            "tdb_plm_custom/static/src/scss/plm_table_header.scss",
            "tdb_plm_custom/static/src/scss/plm_table_cells.scss",
            "tdb_plm_custom/static/src/scss/plm_preview.scss",
            "tdb_plm_custom/static/src/js/plm_realtime_reload.js",
        ],
    },
    "installable": True,
    "application": False,
    "auto_install": False,
}
