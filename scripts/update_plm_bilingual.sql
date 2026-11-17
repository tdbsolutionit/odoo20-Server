-- =============================================================================
-- TDB SOLUTION - HỆ THỐNG ĐỒNG BỘ ĐA NGÔN NGỮ (BILINGUAL EN/VI) CHO ODOOPLM
-- Hỗ trợ chuẩn:
-- 1. Tiếng Anh (en_US): Full 100% tiếng Anh chuẩn quốc tế.
-- 2. Tiếng Việt (vi_VN): Full 100% tiếng Việt kỹ thuật cơ khí trực quan.
-- =============================================================================

DO $$
DECLARE
    m RECORD;
    v_type_menu TEXT;
    v_type_act TEXT;
    v_type_view TEXT;
    v_has_ir_trans BOOLEAN;
    v_arch_en TEXT;
    v_arch_vi TEXT;
BEGIN
    -- -------------------------------------------------------------------------
    -- 1. CẬP NHẬT HỆ THỐNG MENU (ir_ui_menu) - BILINGUAL EN / VI
    -- -------------------------------------------------------------------------
    SELECT data_type INTO v_type_menu FROM information_schema.columns WHERE table_name = 'ir_ui_menu' AND column_name = 'name';
    FOR m IN 
        SELECT d.res_id, 
               CASE d.name 
                   WHEN 'plm_component_menu_view' THEN 'Components'
                   WHEN 'ir_attachment_form' THEN 'Documents'
                   WHEN 'plm_menu_relation' THEN 'Component Relations'
                   WHEN 'plm_menu_dictionaries' THEN 'Configuration'
                   WHEN 'menu_base_component' THEN 'Search on Engineering Parts'
                   WHEN 'menu_document_files' THEN 'Search on Documents'
                   WHEN 'menu_document_relations' THEN 'Search on Document Relations'
                   WHEN 'menu_checkout' THEN 'Search on Checked-Out Documents'
                   WHEN 'menu_backupdoc' THEN 'Search on Backup Data Set'
                   WHEN 'menu_cad_open' THEN 'CAD Opens'
                   WHEN 'menu_cad_open_bck' THEN 'CAD Opens Bck'
                   WHEN 'menu_relation_normal' THEN 'Search on Normal Bill of Materials'
                   WHEN 'menu_description' THEN 'Descriptions'
                   WHEN 'menu_finishing' THEN 'Finishings'
                   WHEN 'menu_treatment' THEN 'Treatments'
                   WHEN 'menu_material' THEN 'Materials'
                   WHEN 'plm_dbthread_id' THEN 'DbThread'
                   WHEN 'plm_groups' THEN 'Groups'
                   WHEN 'plm_product_cad_search' THEN 'Product Cad Search'
                   WHEN 'plm_settings' THEN 'Settings'
                   WHEN 'menu_plm_desktop_token_top' THEN 'Desktop Access Tokens'
                   WHEN 'menu_plm_desktop_token' THEN 'Desktop Access Tokens'
               END as en_name,
               CASE d.name 
                   WHEN 'plm_component_menu_view' THEN 'Linh kiện'
                   WHEN 'ir_attachment_form' THEN 'Tài liệu'
                   WHEN 'plm_menu_relation' THEN 'Quan hệ linh kiện'
                   WHEN 'plm_menu_dictionaries' THEN 'Cấu hình'
                   WHEN 'menu_base_component' THEN 'Tìm kiếm linh kiện'
                   WHEN 'menu_document_files' THEN 'Tìm kiếm tài liệu'
                   WHEN 'menu_document_relations' THEN 'Quan hệ tài liệu'
                   WHEN 'menu_checkout' THEN 'Tài liệu đang Check-out'
                   WHEN 'menu_backupdoc' THEN 'Dữ liệu sao lưu'
                   WHEN 'menu_cad_open' THEN 'Lịch sử mở CAD'
                   WHEN 'menu_cad_open_bck' THEN 'Bản sao lưu mở CAD'
                   WHEN 'menu_relation_normal' THEN 'Tìm kiếm BOM tiêu chuẩn'
                   WHEN 'menu_description' THEN 'Mô tả kỹ thuật'
                   WHEN 'menu_finishing' THEN 'Hoàn thiện bề mặt'
                   WHEN 'menu_treatment' THEN 'Xử lý nhiệt & bề mặt'
                   WHEN 'menu_material' THEN 'Vật liệu chế tạo'
                   WHEN 'plm_dbthread_id' THEN 'Tiến trình nền (DbThread)'
                   WHEN 'plm_groups' THEN 'Nhóm phân quyền'
                   WHEN 'plm_product_cad_search' THEN 'Tìm kiếm CAD linh kiện'
                   WHEN 'plm_settings' THEN 'Cài đặt PLM'
                   WHEN 'menu_plm_desktop_token_top' THEN 'Desktop Access Tokens (Quản lý thiết bị CAD)'
                   WHEN 'menu_plm_desktop_token' THEN 'Desktop Access Tokens'
               END as vi_name
        FROM ir_model_data d
        WHERE (d.module = 'plm' OR d.module = 'tdb_plm_custom') AND d.model = 'ir.ui.menu'
    LOOP
        IF m.en_name IS NOT NULL THEN
            IF v_type_menu = 'jsonb' THEN
                UPDATE ir_ui_menu 
                SET name = jsonb_set(
                    jsonb_set(COALESCE(name, '{}'::jsonb), '{en_US}', to_jsonb(m.en_name)),
                    '{vi_VN}', to_jsonb(m.vi_name)
                ) 
                WHERE id = m.res_id;
            ELSE
                UPDATE ir_ui_menu SET name = m.en_name WHERE id = m.res_id;
            END IF;
        END IF;
    END LOOP;

    -- -------------------------------------------------------------------------
    -- 2. CẬP NHẬT CỬA SỔ ACTION (ir_act_window) - BILINGUAL EN / VI
    -- -------------------------------------------------------------------------
    SELECT data_type INTO v_type_act FROM information_schema.columns WHERE table_name = 'ir_act_window' AND column_name = 'name';
    FOR m IN 
        SELECT d.res_id, 
               CASE d.name 
                   WHEN 'plm_action_document_form' THEN 'Documents'
                   WHEN 'action_plm_component_base' THEN 'Engineering Parts'
                   WHEN 'action_plm_component_base_cad_form' THEN 'Engineering Parts'
                   WHEN 'action_plm_checkout' THEN 'Search on Checked-Out Documents'
                   WHEN 'plm_action_document_relations' THEN 'Search on Document Relations'
                   WHEN 'action_plm_backupdoc' THEN 'Search on Backup Data Set'
                   WHEN 'plm_action_normal_form' THEN 'Search on Normal Bill of Materials'
                   WHEN 'action_plm_desktop_token' THEN 'Desktop Access Tokens'
               END as en_name,
               CASE d.name 
                   WHEN 'plm_action_document_form' THEN 'Tài liệu'
                   WHEN 'action_plm_component_base' THEN 'Linh kiện kỹ thuật'
                   WHEN 'action_plm_component_base_cad_form' THEN 'Linh kiện kỹ thuật'
                   WHEN 'action_plm_checkout' THEN 'Tài liệu đang Check-out'
                   WHEN 'plm_action_document_relations' THEN 'Quan hệ tài liệu'
                   WHEN 'action_plm_backupdoc' THEN 'Dữ liệu sao lưu'
                   WHEN 'plm_action_normal_form' THEN 'Tìm kiếm BOM tiêu chuẩn'
                   WHEN 'action_plm_desktop_token' THEN 'Desktop Access Tokens (Quản lý thiết bị CAD)'
               END as vi_name
        FROM ir_model_data d
        WHERE (d.module = 'plm' OR d.module = 'tdb_plm_custom') AND d.model = 'ir.actions.act_window'
    LOOP
        IF m.en_name IS NOT NULL THEN
            IF v_type_act = 'jsonb' THEN
                UPDATE ir_act_window 
                SET name = jsonb_set(
                    jsonb_set(COALESCE(name, '{}'::jsonb), '{en_US}', to_jsonb(m.en_name)),
                    '{vi_VN}', to_jsonb(m.vi_name)
                ) 
                WHERE id = m.res_id;
            ELSE
                UPDATE ir_act_window SET name = m.en_name WHERE id = m.res_id;
            END IF;
        END IF;
    END LOOP;

    -- -------------------------------------------------------------------------
    -- 3. CẬP NHẬT MÚI GIỜ VIỆT NAM (GMT+7 / Asia/Ho_Chi_Minh)
    -- Cột tz thuộc bảng res_partner (res_users kế thừa qua partner_id)
    -- -------------------------------------------------------------------------
    UPDATE res_partner SET tz = 'Asia/Ho_Chi_Minh';

END $$;
