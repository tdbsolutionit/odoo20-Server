# -*- coding: utf-8 -*-
# Copyright 2026 TDB Solution Co., Ltd.
"""
KỊCH BẢN KIỂM TRA & NÂNG CẤP CHUYÊN BIỆT CHO DMS 20 & TDB_DMS_CUSTOM (UPDATE_DMS)
================================================================================
Chức năng duy nhất:
1. Nhận diện gói DMS 20.0 mới từ OCA (addons/dms/dms).
2. Tự động cấu hình addons_path chuẩn vào odoo.conf.
3. Nạp và kiểm tra tương thích giữa module dms (OCA 20.0) và tdb_dms_custom.
4. Báo cáo chi tiết mọi lỗi phát sinh (nếu có) khi tải models, views, wizards.
"""
import os
import sys
import logging
import http
import http.server

# Chỉ tập trung 2 module theo yêu cầu: DMS 20 OCA & tdb_dms_custom
CUSTOM_MODULES = [
    ('dms', 'Module gốc DMS OCA 20.0 (Quản lý tài liệu kỹ thuật)'),
    ('tdb_dms_custom', 'Tùy biến DMS TDB (Thư mục con, Thùng rác khôi phục file, Bảo mật phân quyền)'),
]

def main():
    print("=" * 80)
    print(">>> KIỂM TRA & NẠP RIÊNG DMS 20 (OCA) VÀ TDB_DMS_CUSTOM (UPDATE_DMS) <<<")
    print("=" * 80)

    # 1. Tạm dừng dịch vụ Odoo cổng 8070 nếu đang chạy
    if os.name == 'nt':
        try:
            import subprocess
            cmd_stop = (
                "Get-NetTCPConnection -LocalPort 8070 -ErrorAction SilentlyContinue | "
                "Select-Object -ExpandProperty OwningProcess -Unique | ForEach-Object { "
                "if ($_ -gt 0) { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue } }; "
                "Get-CimInstance Win32_Process -Filter \"CommandLine LIKE '%run_server.py%'\" | "
                "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
            )
            subprocess.run(["powershell", "-Command", cmd_stop], capture_output=True, timeout=10)
            print("  [OK] Đã tự động tạm dừng dịch vụ Odoo (cổng 8070) để cập nhật an toàn.")
        except Exception as e:
            print(f"  [Lưu ý dừng dịch vụ]: {e}")

    current_dir = os.path.dirname(os.path.abspath(__file__))
    if os.path.basename(current_dir) in ('tdb_dms_custom', 'tdb_plm_custom', 'custom_addons', 'addons'):
        odoo_dir = os.path.abspath(os.path.join(current_dir, '..'))
        while os.path.basename(odoo_dir) in ('custom_addons', 'addons'):
            odoo_dir = os.path.abspath(os.path.join(odoo_dir, '..'))
    else:
        odoo_dir = current_dir

    os.chdir(odoo_dir)

    # Bảo vệ sys.path
    sys.path = [p for p in sys.path if not (os.path.basename(p.rstrip(r'\/')) == 'odoo' and os.path.isfile(os.path.join(p, '__init__.py')))]
    if odoo_dir not in sys.path:
        sys.path.insert(0, odoo_dir)

    try:
        import odoo
        from odoo.tools import config
        try:
            from odoo.modules.registry import Registry
        except ImportError:
            from odoo.orm.registry import Registry
    except ImportError:
        candidate_roots = [
            r'C:\Program Files\Odoo 20.0.20260927\server',
            r'C:\odoo20\server',
            os.path.join(odoo_dir, 'server'),
        ]
        for c in candidate_roots:
            if os.path.exists(os.path.join(c, 'odoo', '__init__.py')) and c not in sys.path:
                sys.path.append(c)
                break
        try:
            import odoo
            from odoo.tools import config
            try:
                from odoo.modules.registry import Registry
            except ImportError:
                from odoo.orm.registry import Registry
        except ImportError as e:
            print(f"[LỖI NGHIÊM TRỌNG]: Không thể nạp thư viện Odoo: {e}.")
            return

    config_candidates = [
        os.path.join(odoo_dir, 'config', 'odoo.conf'),
        os.path.join(odoo_dir, 'odoo.conf'),
        '/etc/odoo/odoo.conf',
        'odoo.conf',
    ]
    config_path = 'odoo.conf'
    for c in config_candidates:
        if os.path.exists(c):
            config_path = c
            break

    print(f"\n[1/4] Kết nối cơ sở dữ liệu odoo20 (cấu hình: {config_path})...")
    try:
        config.parse_config(['-c', config_path], setup_logging=False)
    except TypeError:
        config.parse_config(['-c', config_path])

    # Thiết lập đường dẫn addons_path đầy đủ
    custom_addons_path = os.path.join(odoo_dir, 'custom_addons')
    base_addons_path = os.path.join(odoo_dir, 'addons')
    tdb_custom_path = os.path.join(odoo_dir, 'tdb_custom_addons')
    extra_addons = [tdb_custom_path] if os.path.exists(tdb_custom_path) else []

    # Nhận diện kho dms mới của OCA (addons/dms/dms)
    oca_dms_repo = os.path.normpath(os.path.join(odoo_dir, 'addons', 'dms'))
    if os.path.isdir(os.path.join(oca_dms_repo, 'dms')) and os.path.isfile(os.path.join(oca_dms_repo, 'dms', '__manifest__.py')):
        extra_addons.append(oca_dms_repo)
        print(f"  [OK] Đã phát hiện gói OCA DMS 20.0 tại: {oca_dms_repo}")

    raw_addons = config.get('addons_path') or ''
    if isinstance(raw_addons, list):
        input_paths = [p for p in raw_addons if p]
    elif isinstance(raw_addons, str):
        input_paths = [p.strip() for p in raw_addons.split(',') if p.strip()]
    else:
        input_paths = []

    current_paths = []
    seen = set()
    for p in input_paths + [base_addons_path, custom_addons_path] + extra_addons:
        if os.path.exists(p):
            norm = os.path.normcase(os.path.normpath(os.path.abspath(p)))
            if norm not in seen:
                seen.add(norm)
                current_paths.append(os.path.normpath(p))

    if isinstance(raw_addons, list):
        config['addons_path'] = current_paths
    else:
        config['addons_path'] = ','.join(current_paths)

    # Đảm bảo lưu addons_path vào odoo.conf
    try:
        if os.path.exists(config_path):
            with open(config_path, 'r', encoding='utf-8') as cf_file:
                lines = cf_file.readlines()
            new_lines = []
            found_addons = False
            for line in lines:
                if line.strip().startswith('addons_path'):
                    new_lines.append(f"addons_path = {','.join(current_paths)}\n")
                    found_addons = True
                else:
                    new_lines.append(line)
            if not found_addons:
                new_lines.append(f"\naddons_path = {','.join(current_paths)}\n")
            with open(config_path, 'w', encoding='utf-8') as cf_file:
                cf_file.writelines(new_lines)
            print(f"  [OK] Đã đồng bộ đường dẫn addons_path vào: {config_path}")
    except Exception as e:
        print(f"  [Lưu ý ghi file odoo.conf]: {e}")

    try:
        import odoo.addons
        for p in current_paths:
            if p not in odoo.addons.__path__:
                odoo.addons.__path__.append(p)
    except Exception:
        pass

    try:
        from odoo.modules import initialize_sys_path
        initialize_sys_path()
    except Exception:
        pass

    # Shim tương thích odoo.osv.expression cho Odoo 20
    import types
    if 'odoo.osv' not in sys.modules:
        try:
            from odoo.orm.domains import Domain
            osv_mod = types.ModuleType('odoo.osv')
            osv_mod.expression = Domain
            sys.modules['odoo.osv'] = osv_mod
            sys.modules['odoo.osv.expression'] = Domain
            odoo.osv = osv_mod
        except Exception:
            pass

    db_name = config.get('db_name') or 'odoo20'
    if isinstance(db_name, (list, tuple)):
        db_name = db_name[0]
    registry = Registry(db_name)

    results = {}

    with registry.cursor() as cr:
        env = odoo.api.Environment(cr, odoo.SUPERUSER_ID, {})

        # 2. Cập nhật danh sách module
        print("\n[2/4] Quét danh mục module hệ thống (update_list)...")
        try:
            env['ir.module.module'].update_list()
            print("  [OK] Đã quét thành công các module mới trong danh mục addons.")
        except Exception as e:
            cr.rollback()
            env.invalidate_all()
            print(f"  [Lỗi update_list]: {e}")

        # 3. Nạp và kiểm tra DMS 20 & tdb_dms_custom
        print("\n[3/4] Bắt đầu nạp và nâng cấp DMS 20 & TDB_DMS_CUSTOM...")
        for mod_name, mod_desc in CUSTOM_MODULES:
            print(f"\n  -------------------------------------------------------------------------")
            print(f"  * Xử lý module: [{mod_name}] - {mod_desc}")
            try:
                mod_rec = env['ir.module.module'].search([('name', '=', mod_name)], limit=1)
            except Exception:
                cr.rollback()
                env.invalidate_all()
                mod_rec = env['ir.module.module'].search([('name', '=', mod_name)], limit=1)

            if not mod_rec:
                print(f"    [BỎ QUA]: Không tìm thấy module '{mod_name}' trong addons_path.")
                results[mod_name] = "KHÔNG TÌM THẤY"
                continue

            installed_version = mod_rec.installed_version or mod_rec.latest_version
            print(f"    -> Trạng thái hiện tại: state='{mod_rec.state}', version='{installed_version}'")

            if mod_rec.state == 'installed':
                print(f"    -> Đang NÂNG CẤP (UPGRADE) '{mod_name}'...")
                try:
                    mod_rec.button_immediate_upgrade()
                    cr.commit()
                    print(f"    [THÀNH CÔNG] Đã nâng cấp thành công '{mod_name}'!")
                    results[mod_name] = "NÂNG CẤP THÀNH CÔNG"
                except Exception as e:
                    cr.rollback()
                    env.invalidate_all()
                    print(f"    [LỖI NÂNG CẤP {mod_name}]: {e}")
                    results[mod_name] = f"LỖI: {e}"
                    # Thử lại bằng button_upgrade không tức thì
                    try:
                        mod_rec = env['ir.module.module'].browse(mod_rec.id)
                        mod_rec.button_upgrade()
                        cr.commit()
                    except Exception:
                        cr.rollback()
                        env.invalidate_all()
            else:
                print(f"    -> Đang CÀI ĐẶT MỚI (INSTALL) '{mod_name}'...")
                try:
                    mod_rec.button_immediate_install()
                    cr.commit()
                    print(f"    [THÀNH CÔNG] Đã cài đặt thành công '{mod_name}'!")
                    results[mod_name] = "CÀI ĐẶT THÀNH CÔNG"
                except Exception as e:
                    cr.rollback()
                    env.invalidate_all()
                    print(f"    [LỖI CÀI ĐẶT {mod_name}]: {e}")
                    results[mod_name] = f"LỖI: {e}"
                    try:
                        mod_rec = env['ir.module.module'].browse(mod_rec.id)
                        mod_rec.button_install()
                        cr.commit()
                    except Exception:
                        cr.rollback()
                        env.invalidate_all()

        # 4. Xóa cache assets
        print(f"\n  -------------------------------------------------------------------------")
        print("\n[4/4] Làm sạch bộ nhớ đệm Asset Bundles & Views...")
        try:
            if hasattr(env.registry, 'clear_caches'):
                env.registry.clear_caches()
            elif hasattr(env.registry, 'clear_cache'):
                env.registry.clear_cache()
            cached_assets = env['ir.attachment'].search([
                '|', '|',
                ('url', '=like', '/web/assets/%'),
                ('url', '=like', '%assets_backend%'),
                ('name', '=like', '%.assets_%')
            ])
            if cached_assets:
                count = len(cached_assets)
                cached_assets.unlink()
                print(f"  [OK] Đã xóa {count} tệp cache assets.")
            else:
                print("  [OK] Không có cache assets cũ cần xóa.")
        except Exception as e:
            cr.rollback()
            print(f"  [Lưu ý xóa cache]: {e}")

        cr.commit()

    # Khởi động lại máy chủ Odoo trên cổng 8070
    try:
        import subprocess
        venv_py = os.path.join(odoo_dir, '.venv', 'Scripts', 'python.exe')
        py_exe = venv_py if os.path.isfile(venv_py) else sys.executable
        run_server = os.path.join(odoo_dir, 'odoo-bin')
        cfg = os.path.join(odoo_dir, 'config', 'odoo.conf')
        flags = 0
        if os.name == 'nt':
            flags = subprocess.CREATE_NEW_PROCESS_GROUP | 0x00000008  # DETACHED_PROCESS
        subprocess.Popen(
            [py_exe, run_server, '-c', cfg],
            cwd=odoo_dir,
            creationflags=flags,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=(os.name != 'nt')
        )
        print("  [OK] Đã tự động khởi động máy chủ Odoo trên cổng 8070.")
    except Exception as e:
        print(f"  [Lưu ý khởi động máy chủ]: {e}")

    print("\n" + "=" * 80)
    print(">>> TỔNG KẾT KẾT QUẢ KIỂM TRA DMS 20 & TDB_DMS_CUSTOM <<<")
    print("=" * 80)
    for mod, res in results.items():
        status = "[THÀNH CÔNG]" if "THÀNH CÔNG" in res else "[THẤT BẠI]"
        print(f"  {status} {mod}: {res}")
    print("=" * 80)

if __name__ == '__main__':
    main()
