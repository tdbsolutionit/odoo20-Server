# -*- coding: utf-8 -*-
# Copyright 2026 TDB Solution Co., Ltd.
import os
import sys
import subprocess

def main():
    print("=" * 80)
    print(">>> NÂNG CẤP CHUẨN HÓA TOÀN DIỆN PHÂN HỆ PLM (ODOO ORM NATIVE) <<<")
    print("=" * 80)

    # 1. Tạm dừng dịch vụ Odoo 24/7 để tránh khóa file & bộ nhớ
    if os.name == 'nt':
        print("\n[1/4] Tạm dừng dịch vụ Odoo 24/7...")
        try:
            subprocess.run([
                "powershell", "-Command",
                "Stop-ScheduledTask -TaskName 'Odoo_Server_24_7' -ErrorAction SilentlyContinue"
            ], capture_output=True, timeout=10)
            print("  [OK] Đã tạm dừng Odoo_Server_24_7.")
        except Exception as e:
            print(f"  [Lưu ý dừng task]: {e}")

    # Xác định thư mục gốc odoo
    current_dir = os.path.dirname(os.path.abspath(__file__))
    odoo_dir = os.path.abspath(os.path.join(current_dir, '..')) if os.path.basename(current_dir) in ('scripts', 'custom_addons') else current_dir
    os.chdir(odoo_dir)
    if odoo_dir not in sys.path:
        sys.path.insert(0, odoo_dir)

    # 2. Khởi tạo Odoo ORM & Registry
    print("\n[2/4] Kết nối Registry Odoo 20 (database: odoo20)...")
    import odoo
    from odoo.tools import config
    try:
        from odoo.modules.registry import Registry
    except ImportError:
        from odoo.orm.registry import Registry

    config_candidates = [
        os.path.join(odoo_dir, 'config', 'odoo.conf'),
        os.path.join(odoo_dir, 'odoo.conf'),
        'odoo.conf',
    ]
    config_path = 'odoo.conf'
    for c in config_candidates:
        if os.path.exists(c):
            config_path = c
            break

    try:
        config.parse_config(['-c', config_path, '-d', 'odoo20'], setup_logging=False)
    except TypeError:
        config.parse_config(['-c', config_path, '-d', 'odoo20'])

    db_name = config.get('db_name') or 'odoo20'
    if isinstance(db_name, (list, tuple)):
        db_name = db_name[0]

    registry = Registry(db_name)

    with registry.cursor() as cr:
        env = odoo.api.Environment(cr, odoo.SUPERUSER_ID, {})

        # Bước 2.0: Cập nhật múi giờ Việt Nam (GMT+7 / Asia/Ho_Chi_Minh) cho toàn bộ người dùng
        # Lưu ý: Trong Odoo, cột tz nằm ở bảng res_partner (res_users liên kết qua partner_id)
        print("\n[2.0] Đồng bộ múi giờ Việt Nam (GMT+7 / Asia/Ho_Chi_Minh) cho toàn bộ tài khoản...")
        try:
            cr.execute("""
                UPDATE res_partner SET tz = 'Asia/Ho_Chi_Minh';
            """)
            cr.commit()
            print("  [OK] Đã thiết lập múi giờ Asia/Ho_Chi_Minh (GMT+7) cho toàn bộ đối tác và người dùng.")
        except Exception as e:
            cr.rollback()
            print(f"  [Lưu ý múi giờ]: {e}")

        # Bước 2.1: Đồng bộ hóa toàn bộ Menu và Action PLM song ngữ
        print("\n[2.1] Đồng bộ hóa Menus và Actions song ngữ (EN/VI)...")
        sql_file = os.path.join(odoo_dir, 'scripts', 'update_plm_bilingual.sql')
        if os.path.exists(sql_file):
            with open(sql_file, 'r', encoding='utf-8') as f:
                sql_content = f.read()
            try:
                cr.execute(sql_content)
                cr.commit()
                print("  [OK] Đã thực thi scripts/update_plm_bilingual.sql thành công.")
            except Exception as e:
                cr.rollback()
                print(f"  [Lưu ý SQL bilingual]: {e}")

        # Bước 2.1b: Khôi phục cấu trúc phân cấp Menu PLM (thu gọn icon về bên trong ứng dụng PLM)
        print("\n[2.1b] Thu gọn toàn bộ Menu con PLM về đúng cây phân cấp (tránh hiện tràn icon ngoài màn hình)...")
        try:
            cr.execute("""
                UPDATE ir_ui_menu m
                SET parent_id = (SELECT res_id FROM ir_model_data WHERE module = 'plm' AND name = 'plm_menu' AND model = 'ir.ui.menu' LIMIT 1)
                WHERE id IN (
                    SELECT res_id FROM ir_model_data 
                    WHERE module = 'plm' AND model = 'ir.ui.menu' 
                    AND name IN ('plm_component_menu_view', 'ir_attachment_form', 'plm_menu_relation', 'plm_menu_dictionaries')
                );

                UPDATE ir_ui_menu m
                SET parent_id = (SELECT res_id FROM ir_model_data WHERE module = 'plm' AND name = 'plm_component_menu_view' AND model = 'ir.ui.menu' LIMIT 1)
                WHERE id IN (
                    SELECT res_id FROM ir_model_data 
                    WHERE module = 'plm' AND model = 'ir.ui.menu' 
                    AND name IN ('menu_base_component')
                );

                UPDATE ir_ui_menu m
                SET parent_id = (SELECT res_id FROM ir_model_data WHERE module = 'plm' AND name = 'ir_attachment_form' AND model = 'ir.ui.menu' LIMIT 1)
                WHERE id IN (
                    SELECT res_id FROM ir_model_data 
                    WHERE module = 'plm' AND model = 'ir.ui.menu' 
                    AND name IN ('menu_document_files', 'menu_document_relations', 'menu_checkout', 'menu_backupdoc', 'menu_cad_open', 'menu_cad_open_bck')
                );

                UPDATE ir_ui_menu m
                SET parent_id = (SELECT res_id FROM ir_model_data WHERE module = 'plm' AND name = 'plm_menu_relation' AND model = 'ir.ui.menu' LIMIT 1)
                WHERE id IN (
                    SELECT res_id FROM ir_model_data 
                    WHERE module = 'plm' AND model = 'ir.ui.menu' 
                    AND name IN ('menu_relation_normal')
                );

                UPDATE ir_ui_menu m
                SET parent_id = (SELECT res_id FROM ir_model_data WHERE module = 'plm' AND name = 'plm_menu_dictionaries' AND model = 'ir.ui.menu' LIMIT 1)
                WHERE id IN (
                    SELECT res_id FROM ir_model_data 
                    WHERE module = 'plm' AND model = 'ir.ui.menu' 
                    AND name IN ('menu_description', 'menu_finishing', 'menu_treatment', 'menu_material', 'plm_dbthread_id', 'plm_groups', 'plm_product_cad_search', 'plm_settings')
                );
            """)
            cr.commit()
            print("  [OK] Đã thu gọn toàn bộ Menu PLM về đúng vị trí!")
        except Exception as e:
            cr.rollback()
            print(f"  [Lưu ý gom menu]: {e}")

        # Bước 2.2: Giải phóng cờ noupdate để Odoo ORM nạp lại đè toàn bộ views từ file XML
        print("\n[2.2] Giải phóng cờ noupdate cho toàn bộ view PLM...")
        try:
            cr.execute("""
                UPDATE ir_model_data 
                SET noupdate = false 
                WHERE module IN ('plm', 'tdb_plm_custom') AND model = 'ir.ui.view';
            """)
            cr.commit()
            print("  [OK] Đã mở khóa noupdate = false.")
        except Exception as e:
            cr.rollback()
            print(f"  [Lưu ý noupdate]: {e}")

        # 3. Quét và nâng cấp trực tiếp qua Odoo ORM (chuẩn native 100%)
        print("\n[3/4] Quét và Nâng cấp module qua Odoo ORM...")
        try:
            env['ir.module.module'].update_list()
            cr.commit()
        except Exception as e:
            cr.rollback()
            print(f"  [Lưu ý update_list]: {e}")

        # Nâng cấp module plm gốc trước
        mod_plm = env['ir.module.module'].search([('name', '=', 'plm')], limit=1)
        if mod_plm and mod_plm.state == 'installed':
            print("  * Đang nâng cấp nạp lại module plm gốc...")
            try:
                mod_plm.button_immediate_upgrade()
                cr.commit()
                print("  [OK] Nâng cấp module plm thành công!")
            except Exception as e:
                cr.rollback()
                print(f"  [Lỗi nâng cấp plm]: {e}")

        # Đảm bảo cả 2 ngôn ngữ en_US và vi_VN đều được kích hoạt trong res_lang
        try:
            cr.execute("UPDATE res_lang SET active = true WHERE code IN ('en_US', 'vi_VN');")
            cr.commit()
            print("  [OK] Đã kích hoạt cả 2 ngôn ngữ en_US và vi_VN trong res_lang.")
        except Exception as e:
            cr.rollback()
            print(f"  [Lưu ý res_lang]: {e}")

        # Nâng cấp module tdb_plm_custom
        mod_tdb = env['ir.module.module'].search([('name', '=', 'tdb_plm_custom')], limit=1)
        if mod_tdb:
            print("  * Đang nâng cấp nạp lại module tdb_plm_custom (Nút Lock & Banners)...")
            try:
                mod_tdb.button_immediate_upgrade()
                cr.commit()
                print("  [OK] Nâng cấp module tdb_plm_custom thành công rực rỡ!")
            except Exception as e:
                cr.rollback()
                print(f"  [Lỗi nâng cấp tdb_plm_custom]: {e}")

        # Nâng cấp module custom_home_menu (bộ chuyển đổi ngôn ngữ)
        mod_home = env['ir.module.module'].search([('name', '=', 'custom_home_menu')], limit=1)
        if mod_home and mod_home.state == 'installed':
            print("  * Đang nâng cấp module custom_home_menu...")
            try:
                mod_home.button_immediate_upgrade()
                cr.commit()
                print("  [OK] Nâng cấp module custom_home_menu thành công!")
            except Exception as e:
                cr.rollback()
                print(f"  [Lỗi nâng cấp custom_home_menu]: {e}")

        # Bước 3.1: Đồng bộ hóa toàn diện đa ngôn ngữ (Bilingual EN/VI) vào ir_ui_view.arch_db
        print("\n[3.1] Đồng bộ hóa bản dịch Tiếng Việt (vi_VN) và Tiếng Anh (en_US) trực tiếp vào ir_ui_view.arch_db...")
        try:
            import json

            # Bản đồ làm sạch tiếng Anh chuẩn quốc tế (loại bỏ tiếng Việt trong bản en_US)
            clean_en_map = [
                # Tabs: hỗ trợ cả attribute và string
                ('<attribute name="string">Tài liệu đính kèm</attribute>', '<attribute name="string">Attachment</attribute>'),
                ('<attribute name="string">Lịch sử luồng công việc</attribute>', '<attribute name="string">Workflow History</attribute>'),
                ('<attribute name="string">Linh kiện liên kết</attribute>', '<attribute name="string">Linked Parts</attribute>'),
                ('<attribute name="string">Nội dung chỉ mục</attribute>', '<attribute name="string">Indexed Content</attribute>'),
                ('<attribute name="string">Ghi chú</attribute>', '<attribute name="string">Notes</attribute>'),
                ('<attribute name="string">Lịch sử phiên bản</attribute>', '<attribute name="string">Version History</attribute>'),
                ('<attribute name="string">Tài liệu tham chiếu</attribute>', '<attribute name="string">Referenced Documents</attribute>'),
                ('string="Tài liệu đính kèm"', 'string="Attachment"'),
                ('string="Lịch sử luồng công việc"', 'string="Workflow History"'),
                ('string="Linh kiện liên kết"', 'string="Linked Parts"'),
                ('string="Nội dung chỉ mục"', 'string="Indexed Content"'),
                ('string="Ghi chú"', 'string="Notes"'),
                ('string="Lịch sử phiên bản"', 'string="Version History"'),
                ('string="Tài liệu tham chiếu"', 'string="Referenced Documents"'),
                ('string="File liên quan (References)"', 'string="Referenced Documents"'),
                ('string="File liên quan"', 'string="Referenced Documents"'),
                ('string="Referenced Documents (References)"', 'string="Referenced Documents"'),

                # Buttons: Lock, Unlock, Force Unlock (Giữ nguyên không dịch)
                ('string="Khóa lấy Key"', 'string="Lock"'),
                ('string="Khóa (Lock)"', 'string="Lock"'),
                ('string="Nhả Key (Unlock)"', 'string="Unlock"'),
                ('string="Nhả Key"', 'string="Unlock"'),
                ('string="Mở khóa (Unlock)"', 'string="Unlock"'),
                ('string="Thu hồi Key (Force Unlock)"', 'string="Force Unlock"'),
                ('string="Thu hồi Key"', 'string="Force Unlock"'),

                # Wizard
                ('string="Bàn giao &amp; Thu hồi quyền giữ Key"', 'string="Transfer &amp; Release Key Holder"'),
                ('string="Bàn giao &amp; Mở khóa quyền giữ Key"', 'string="Transfer &amp; Release Key Holder"'),
                ('string="Chuyển giao cho kỹ sư khác"', 'string="Transfer to another engineer"'),
                ('string="Bàn giao cho kỹ sư khác"', 'string="Transfer to another engineer"'),
                ('string="Người nhận Key mới"', 'string="New Key Holder"'),
                ('string="Kỹ sư nhận Key mới"', 'string="New Key Holder"'),
                ('string="Người đang giữ Key"', 'string="Current Key Holder"'),
                ('string="Thông tin tài liệu"', 'string="Document Information"'),
                ('string="Loại thao tác"', 'string="Action Type"'),
                ('string="Thao tác"', 'string="Action"'),

                # Banners
                ('<span>Người giữ Key: </span>', '<span>Key Holder: </span>'),
                ('(Người giữ Key có quyền độc quyền Check-in cập nhật file)', '(Key holder has exclusive permission to Check-in)'),
                ('(Người giữ Key có quyền độc quyền Check-in cập nhật bản vẽ)', '(Key holder has exclusive permission to Check-in)'),
                ('Bạn đang giữ Key', 'You hold the Key'),
                ('Chế độ tham chiếu', 'Reference Mode'),
                ('<span>Trạng thái tài liệu: </span><span class="fw-bold">Chưa có người giữ Key</span>', '<span>Document status: </span><span class="fw-bold">No Key Holder</span>'),
                ('(Bấm nút \'Lock\' trên thanh công cụ để nhận Key)', '(Click \'Lock\' button on the header to claim Key)'),
                ('(Bấm nút \'Lock\' trên thanh tác vụ để nhận Key)', '(Click \'Lock\' button on the header to claim Key)'),
                ('Chờ nhận Key', 'Waiting for Lock'),
                ('Tài liệu đã ban hành (Chỉ đọc)', 'Document Released (Read-Only)'),
                ('(Tài liệu đã ban hành/lỗi thời và bị khóa hoàn toàn, không thể sửa đổi hoặc Check-in)', '(Document is released/obsoleted and locked against modification or check-in)'),
            ]

            # Bản đồ dịch sang tiếng Việt 100% chuẩn kỹ thuật PLM
            translations_map = [
                # Tabs (Toàn bộ 7 Tab trên Form view Document - hỗ trợ cả attribute và string)
                ('<attribute name="string">Attachment</attribute>', '<attribute name="string">Tài liệu đính kèm</attribute>'),
                ('string="Attachment"', 'string="Tài liệu đính kèm"'),

                ('<attribute name="string">Workflow History</attribute>', '<attribute name="string">Lịch sử luồng công việc</attribute>'),
                ('string="Workflow History"', 'string="Lịch sử luồng công việc"'),
                ('string="History"', 'string="Lịch sử luồng công việc"'),

                ('<attribute name="string">Linked Parts</attribute>', '<attribute name="string">Linh kiện liên kết</attribute>'),
                ('string="Linked Parts"', 'string="Linh kiện liên kết"'),

                ('<attribute name="string">Indexed Content</attribute>', '<attribute name="string">Nội dung chỉ mục</attribute>'),
                ('string="Indexed Content"', 'string="Nội dung chỉ mục"'),

                ('<attribute name="string">Notes</attribute>', '<attribute name="string">Ghi chú</attribute>'),
                ('string="Notes"', 'string="Ghi chú"'),

                ('<attribute name="string">Version History</attribute>', '<attribute name="string">Lịch sử phiên bản</attribute>'),
                ('string="Version History"', 'string="Lịch sử phiên bản"'),

                ('<attribute name="string">Referenced Documents</attribute>', '<attribute name="string">Tài liệu tham chiếu</attribute>'),
                ('string="Referenced Documents"', 'string="Tài liệu tham chiếu"'),
                ('string="Referenced Documents (References)"', 'string="Tài liệu tham chiếu"'),

                # Wizard Bàn giao & Mở khóa Key (plm.transfer.key.wizard)
                ('string="Transfer &amp; Release Key Holder"', 'string="Bàn giao &amp; Mở khóa quyền giữ Key"'),
                ('string="Transfer & Release Key Holder"', 'string="Bàn giao & Mở khóa quyền giữ Key"'),
                ('string="Document Information"', 'string="Thông tin tài liệu"'),
                ('string="Action Type"', 'string="Loại thao tác"'),
                ('string="Action"', 'string="Thao tác"'),
                ('string="Reason (Optional)"', 'string="Lý do (Tùy chọn)"'),
                ('string="Transfer to another engineer"', 'string="Bàn giao cho kỹ sư khác"'),
                ('string="New Key Holder"', 'string="Kỹ sư nhận Key mới"'),
                ('string="Current Key Holder"', 'string="Người đang giữ Key"'),
                ('string="Release All Check-outs"', 'string="Giải phóng tất cả phiên Check-out"'),
                ('string="Release All Current Check-outs"', 'string="Giải phóng tất cả phiên Check-out"'),
                ('placeholder="Enter reason for transfer or unlock (optional)..."', 'placeholder="Nhập lý do chuyển giao hoặc mở khóa (tùy chọn)..."'),
                ('Transfer Check-in permission (Key Holder) to another engineer, or Force Unlock to release the document.', 'Bàn giao quyền Check-in (giữ Key) cho kỹ sư khác hoặc Force Unlock để giải phóng tài liệu.'),
                ('Transfer Check-in permission (Key Holder) to another engineer, or Unlock to release the document.', 'Bàn giao quyền Check-in (giữ Key) cho kỹ sư khác hoặc Unlock để giải phóng tài liệu.'),
                ('string="Confirm"', 'string="Xác nhận"'),
                ('string="Cancel"', 'string="Hủy bỏ"'),

                # Header Buttons (Quy định: Lock, Unlock, Force Unlock giữ nguyên không dịch ở bất kỳ ngôn ngữ nào)
                ('string="Khóa lấy Key"', 'string="Lock"'),
                ('string="Khóa (Lock)"', 'string="Lock"'),
                ('string="Nhả Key (Unlock)"', 'string="Unlock"'),
                ('string="Nhả Key"', 'string="Unlock"'),
                ('string="Mở khóa (Unlock)"', 'string="Unlock"'),
                ('string="Thu hồi Key (Force Unlock)"', 'string="Force Unlock"'),
                ('string="Thu hồi Key"', 'string="Force Unlock"'),
                ('string="Lock"', 'string="Lock"'),
                ('string="Unlock"', 'string="Unlock"'),
                ('string="Force Unlock"', 'string="Force Unlock"'),
                ('string="Check-out"', 'string="Check-out"'),
                ('string="Check-in"', 'string="Check-in"'),
                ('string="Undo Check-out"', 'string="Hủy Check-out"'),
                ('string="Confirm Document"', 'string="Xác nhận tài liệu"'),
                ('string="Release Document"', 'string="Ban hành tài liệu"'),
                ('string="Correct Document"', 'string="Sửa đổi tài liệu"'),
                ('string="Un Release"', 'string="Hủy ban hành"'),
                ('string="Re Release"', 'string="Ban hành lại"'),

                # Groups & Fields (hỗ trợ cả attribute và inline string)
                ('<attribute name="string">File Data</attribute>', '<attribute name="string">Dữ liệu tệp</attribute>'),
                ('string="File Data"', 'string="Dữ liệu tệp"'),
                ('<attribute name="string">File Content (base64)</attribute>', '<attribute name="string">Nội dung tệp (base64)</attribute>'),
                ('string="File Content (base64)"', 'string="Nội dung tệp (base64)"'),
                ('<attribute name="string">File Name</attribute>', '<attribute name="string">Tên tệp</attribute>'),
                ('string="File Name"', 'string="Tên tệp"'),
                ('<attribute name="string">Document Type</attribute>', '<attribute name="string">Loại tài liệu</attribute>'),
                ('string="Document Type"', 'string="Loại tài liệu"'),
                ('<attribute name="string">Website</attribute>', '<attribute name="string">Trang web</attribute>'),
                ('string="Website"', 'string="Trang web"'),
                ('<attribute name="string">Related To</attribute>', '<attribute name="string">Liên quan đến</attribute>'),
                ('string="Related To"', 'string="Liên quan đến"'),
                ('<attribute name="string">Resource ID</attribute>', '<attribute name="string">ID tài nguyên</attribute>'),
                ('string="Resource ID"', 'string="ID tài nguyên"'),
                ('<attribute name="string">Attachments To</attribute>', '<attribute name="string">Đính kèm vào</attribute>'),
                ('string="Attachments To"', 'string="Đính kèm vào"'),
                ('<attribute name="string">Resource Name</attribute>', '<attribute name="string">Tên tài nguyên</attribute>'),
                ('string="Resource Name"', 'string="Tên tài nguyên"'),
                ('<attribute name="string">Resource Model</attribute>', '<attribute name="string">Mô hình tài nguyên</attribute>'),
                ('string="Resource Model"', 'string="Mô hình tài nguyên"'),
                ('<attribute name="string">Changes</attribute>', '<attribute name="string">Nhật ký thay đổi</attribute>'),
                ('string="Changes"', 'string="Nhật ký thay đổi"'),
                ('<attribute name="string">Creation</attribute>', '<attribute name="string">Khởi tạo</attribute>'),
                ('string="Creation"', 'string="Khởi tạo"'),
                ('<attribute name="string">Last Update</attribute>', '<attribute name="string">Cập nhật</attribute>'),
                ('string="Last Update"', 'string="Cập nhật"'),
                ('<attribute name="string">Workflow User</attribute>', '<attribute name="string">Người duyệt kỹ thuật</attribute>'),
                ('string="Workflow User"', 'string="Người duyệt kỹ thuật"'),
                ('<attribute name="string">Released By</attribute>', '<attribute name="string">Người ban hành</attribute>'),
                ('string="Released By"', 'string="Người ban hành"'),
                ('<attribute name="string">Revision By</attribute>', '<attribute name="string">Người nâng phiên bản</attribute>'),
                ('string="Revision By"', 'string="Người nâng phiên bản"'),
                ('<attribute name="string">Modification Reason</attribute>', '<attribute name="string">Lý do sửa đổi</attribute>'),
                ('string="Modification Reason"', 'string="Lý do sửa đổi"'),
                ('<attribute name="string">Description</attribute>', '<attribute name="string">Mô tả</attribute>'),
                ('string="Description"', 'string="Mô tả"'),
                ('<attribute name="string">Child Components (References / Assembly)</attribute>', '<attribute name="string">Chi tiết con (Tham chiếu / Cụm lắp ráp)</attribute>'),
                ('string="Child Components (References / Assembly)"', 'string="Chi tiết con (Tham chiếu / Cụm lắp ráp)"'),
                ('<attribute name="string">Where Used (Parents)</attribute>', '<attribute name="string">Nơi sử dụng (Cụm máy cha / Where-Used)</attribute>'),
                ('string="Where Used (Parents)"', 'string="Nơi sử dụng (Cụm máy cha / Where-Used)"'),
                ('string="Related Child Components"', 'string="Chi tiết con liên quan"'),
                ('string="Related Parent Assemblies"', 'string="Cụm máy cha liên quan"'),
                ('string="Child Document"', 'string="Tài liệu con"'),
                ('string="Parent Document"', 'string="Tài liệu cha"'),
                ('string="Lock State"', 'string="Trạng thái khóa"'),
                ('string="Key Holder"', 'string="Người giữ Key"'),
                ('<attribute name="string">PLM Information</attribute>', '<attribute name="string">Thông tin PLM</attribute>'),
                ('string="PLM Information"', 'string="Thông tin PLM"'),
                ('<attribute name="string">Eng. Document Name</attribute>', '<attribute name="string">Mã tài liệu PLM</attribute>'),
                ('string="Eng. Document Name"', 'string="Mã tài liệu PLM"'),
                ('<attribute name="string">Engineering Revision</attribute>', '<attribute name="string">Phiên bản kỹ thuật</attribute>'),
                ('string="Engineering Revision"', 'string="Phiên bản kỹ thuật"'),
                ('<attribute name="string">Is a PLM Document</attribute>', '<attribute name="string">Là tài liệu PLM</attribute>'),
                ('string="Is a PLM Document"', 'string="Là tài liệu PLM"'),
                ('<attribute name="string">PLM Access</attribute>', '<attribute name="string">Quyền truy cập PLM</attribute>'),
                ('string="PLM Access"', 'string="Quyền truy cập PLM"'),
                ('<attribute name="string">Doc Structure</attribute>', '<attribute name="string">Cấu trúc tài liệu</attribute>'),
                ('string="Doc Structure"', 'string="Cấu trúc tài liệu"'),

                # Document List
                ('string="Documents"', 'string="Tài liệu"'),
                ('string="Preview"', 'string="Xem trước"'),
                ('string="Name"', 'string="Tên tài liệu"'),
                ('string="Revision"', 'string="Phiên bản"'),
                ('string="Engineering Status"', 'string="Trạng thái kỹ thuật"'),
                ('string="Engineering State"', 'string="Trạng thái kỹ thuật"'),
                ('string="Engineering Code"', 'string="Mã kỹ thuật"'),
                ('string="Created by"', 'string="Người tạo"'),
                ('string="Created on"', 'string="Ngày tạo"'),
                ('string="Last Updated by"', 'string="Người cập nhật"'),
                ('string="Last Updated on"', 'string="Ngày cập nhật"'),

                # Component List
                ('string="Engineering Parts"', 'string="Linh kiện kỹ thuật"'),
                ('string="Image"', 'string="Hình ảnh"'),
                ('string="Configuration Name"', 'string="Tên cấu hình"'),
                ('string="Internal Reference"', 'string="Mã nội bộ"'),
                ('string="Description"', 'string="Mô tả"'),
                ('string="Writable"', 'string="Có thể ghi"'),
                ('string="CAD Raw Material"', 'string="Vật liệu CAD"'),
                ('string="CAD Surface Finishing"', 'string="Hoàn thiện bề mặt CAD"'),
                ('string="Linked Documents"', 'string="Tài liệu liên kết"'),
                ('string="Checked-Out By"', 'string="Được Check-Out bởi"'),

                # Document History
                ('string="Document File History"', 'string="Lịch sử phiên bản tệp"'),
                ('string="Document"', 'string="Tài liệu"'),
                ('string="Version"', 'string="Phiên bản"'),
                ('string="Size"', 'string="Dung lượng"'),
                ('string="User"', 'string="Người thực hiện"'),
                ('string="Date"', 'string="Thời điểm lưu"'),
                ('string="Note"', 'string="Ghi chú"'),
                ('string="Restore"', 'string="Khôi phục"'),
                ('string="Download"', 'string="Tải về"'),
                ('string="Document History Detail"', 'string="Chi tiết phiên bản lịch sử"'),
                ('string="Restore to this Version"', 'string="Khôi phục về phiên bản này"'),
                ('string="Version Information"', 'string="Thông tin phiên bản"'),
                ('string="Log"', 'string="Nhật ký lưu"'),
                ('string="Modification Note"', 'string="Ghi chú thay đổi"'),

                # Banners
                ('<span>Key Holder: </span>', '<span>Người giữ Key: </span>'),
                ('(Key holder has exclusive permission to Check-in)', '(Người giữ Key có quyền độc quyền Check-in cập nhật bản vẽ)'),
                ('You hold the Key', 'Bạn đang giữ Key'),
                ('Reference Mode', 'Chế độ tham chiếu'),
                ('<span>Document status: </span><span class="fw-bold">No Key Holder</span>', '<span>Trạng thái tài liệu: </span><span class="fw-bold">Chưa có người giữ Key</span>'),
                ('(Click \'Lock\' button on the header to claim Key)', '(Bấm nút \'Lock\' trên thanh tác vụ để nhận Key)'),
                ('Waiting for Lock', 'Chờ nhận Key'),
                ('Document Released (Read-Only)', 'Tài liệu đã ban hành (Chỉ đọc)'),
                ('(Document is released/obsoleted and locked against modification or check-in)', '(Tài liệu đã ban hành/lỗi thời và bị khóa hoàn toàn, không thể sửa đổi hoặc Check-in)'),
            ]

            # Lấy toàn bộ view thuộc tdb_plm_custom và các view PLM cốt lõi
            cr.execute("""
                SELECT v.id, v.arch_db 
                FROM ir_ui_view v
                JOIN ir_model_data d ON d.res_id = v.id AND d.model = 'ir.ui.view'
                WHERE d.module = 'tdb_plm_custom'
                   OR (d.module = 'plm' AND d.name IN (
                       'view_attachment_form_plm_hinerit', 
                       'ir_attachment_list', 
                       'ir_attachment_search', 
                       'document_kanban_view', 
                       'plm_component_form_view', 
                       'plm_component_list_view'
                   ));
            """)
            views = cr.fetchall()
            updated_count = 0
            for view_id, arch_db in views:
                if not arch_db:
                    continue
                # Xác định nội dung arch tiếng Anh gốc
                if isinstance(arch_db, dict):
                    arch_en = arch_db.get('en_US') or arch_db.get('vi_VN') or ''
                elif isinstance(arch_db, str):
                    try:
                        arch_json = json.loads(arch_db)
                        if isinstance(arch_json, dict):
                            arch_en = arch_json.get('en_US') or arch_json.get('vi_VN') or ''
                        else:
                            arch_en = arch_db
                    except Exception:
                        arch_en = arch_db
                else:
                    arch_en = str(arch_db)

                if not arch_en:
                    continue

                # 1. Làm sạch bản tiếng Anh (loại bỏ từ tiếng Việt thừa để khi sang English là 100% tiếng Anh)
                for vi_bad, en_good in clean_en_map:
                    arch_en = arch_en.replace(vi_bad, en_good)

                # 2. Tạo bản tiếng Việt bằng cách thay thế các chuỗi hiển thị
                arch_vi = arch_en
                for en_str, vi_str in translations_map:
                    arch_vi = arch_vi.replace(en_str, vi_str)

                # Ghi lại cấu trúc song ngữ hoàn hảo: en_US = chuẩn quốc tế, vi_VN = tiếng Việt
                new_arch_json = {
                    'en_US': arch_en,
                    'vi_VN': arch_vi
                }
                cr.execute("UPDATE ir_ui_view SET arch_db = %s WHERE id = %s", (json.dumps(new_arch_json), view_id))
                updated_count += 1

            cr.commit()
            print(f"  [OK] Đã cập nhật thành công {updated_count} views sang song ngữ EN/VI hoàn hảo!")
        except Exception as e:
            cr.rollback()
            print(f"  [Lưu ý đồng bộ view song ngữ]: {e}")

        # Xóa sạch toàn bộ cache assets để trình duyệt nhận ngay giao diện mới
        try:
            cr.execute("DELETE FROM ir_attachment WHERE url LIKE '%.assets_%' OR name LIKE '%.assets_%';")
            cr.commit()
            print("  [OK] Đã xóa sạch bộ nhớ đệm web assets cũ.")
        except Exception as e:
            cr.rollback()
            print(f"  [Lưu ý xóa assets]: {e}")

        # Xóa cache views
        try:
            env['ir.ui.view'].clear_caches()
            env.registry.clear_cache()
            cr.commit()
            print("  [OK] Đã làm mới bộ nhớ đệm view (clear_caches).")
        except Exception as e:
            cr.rollback()
            print(f"  [Lưu ý clear cache]: {e}")

        cr.commit()

    # 4. Khởi động lại dịch vụ Odoo 24/7
    if os.name == 'nt':
        print("\n[4/4] Khởi động lại dịch vụ Odoo 24/7...")
        try:
            subprocess.run([
                "powershell", "-Command",
                "Start-ScheduledTask -TaskName 'Odoo_Server_24_7' -ErrorAction SilentlyContinue"
            ], capture_output=True, timeout=10)
            print("  [OK] Dịch vụ Odoo 24/7 đã khởi động lại hoàn tất!")
        except Exception as e:
            print(f"  [Lưu ý khởi động task]: {e}")

    print("\n" + "=" * 80)
    print(">>> HOÀN TẤT NÂNG CẤP! VUI LÒNG BẤM CTRL + F5 TRÊN TRÌNH DUYỆT ĐỂ XEM KẾT QUẢ <<<")
    print("=" * 80)

if __name__ == '__main__':
    main()
