# -*- coding: utf-8 -*-
import base64
import hashlib
import logging
import os
import subprocess
import tempfile
from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

try:
    from odoo.orm.domains import Domain as expression
except ImportError:
    from odoo.osv import expression

_logger = logging.getLogger(__name__)

OFFICE_EXTENSIONS = {
    # Word
    "doc", "docx", "dot", "dotx", "odt", "rtf",
    # Excel
    "xls", "xlsx", "xlsm", "xlt", "xltx", "ods", "csv", "tsv",
    # PowerPoint
    "ppt", "pptx", "pps", "ppsx", "pot", "potx", "odp",
}

EXCEL_EXTENSIONS = {
    "xls", "xlsx", "xlsm", "xlt", "xltx", "ods", "csv", "tsv"
}

PDF_PREVIEW_DIR = "/tmp/odoo_dms_pdf_cache"


class DmsFile(models.Model):
    _inherit = "dms.file"

    is_deleted = fields.Boolean(
        string="Đã chuyển vào Thùng Rác",
        default=False,
        index=True,
        tracking=True,
    )
    uploader_name = fields.Char(
        string="Tên người đăng tải",
        related="create_uid.name",
        store=True,
        readonly=True,
    )
    deleted_uid = fields.Many2one(
        comodel_name="res.users",
        string="Người đã xóa",
        readonly=True,
        tracking=True,
        index=True,
    )
    deleted_user_name = fields.Char(
        string="Tên người xóa",
        related="deleted_uid.name",
        store=True,
        readonly=True,
    )
    deleted_date = fields.Datetime(
        string="Thời gian xóa",
        readonly=True,
        tracking=True,
    )
    original_directory_id = fields.Many2one(
        comodel_name="dms.directory",
        string="Thư mục ban đầu",
        readonly=True,
        index=True,
    )
    is_in_recycle_bin = fields.Boolean(
        string="Nằm trong Thùng Rác",
        compute="_compute_is_in_recycle_bin",
        store=True,
        index=True,
    )
    is_downloadable = fields.Boolean(
        string="Cho phép tải về",
        default=True,
        tracking=True,
        help="Nếu bỏ chọn, chỉ Leader hoặc Sếp/Admin mới được tải tệp này về máy.",
    )
    is_sharing = fields.Boolean(
        string="Phòng ban Chia sẻ",
        related="directory_id.is_sharing",
        store=True,
    )
    can_download = fields.Boolean(
        string="Được phép tải về",
        compute="_compute_can_download",
        help="Xác định người dùng hiện tại có quyền tải tệp tin này về máy hay không.",
    )
    can_manage_download = fields.Boolean(
        string="Có quyền sửa cấu hình tải về",
        compute="_compute_can_manage_download",
        help="Chỉ Leader của phòng ban hoặc Sếp/Admin mới có quyền bật/tắt quyền tải về.",
    )
    preview_html = fields.Html(
        string="Bản xem trước",
        compute="_compute_preview_html",
        sanitize=False,
    )

    @api.depends("is_downloadable", "directory_id", "directory_id.is_sharing", "directory_id.leader_ids")
    def _compute_can_download(self):
        user = self.env.user
        is_admin = user._is_superuser() or user.has_group("base.group_system")
        for record in self:
            if is_admin:
                record.can_download = True
                continue
            dir_rec = record.directory_id
            is_sharing = dir_rec._is_sharing_directory() if dir_rec and hasattr(dir_rec, '_is_sharing_directory') else False
            if is_sharing:
                record.can_download = bool(record.is_downloadable)
            else:
                is_leader = dir_rec._is_user_leader(user) if dir_rec and hasattr(dir_rec, '_is_user_leader') else False
                if is_leader:
                    record.can_download = True
                else:
                    record.can_download = bool(record.is_downloadable)

    @api.depends("directory_id", "directory_id.is_sharing", "directory_id.leader_ids")
    def _compute_can_manage_download(self):
        user = self.env.user
        is_admin = user._is_superuser() or user.has_group("base.group_system")
        for record in self:
            if is_admin:
                record.can_manage_download = True
                continue
            dir_rec = record.directory_id
            if not dir_rec:
                record.can_manage_download = False
                continue
            record.can_manage_download = bool(dir_rec._is_user_leader(user)) if hasattr(dir_rec, '_is_user_leader') else False

    @api.depends("directory_id", "directory_id.is_recycle_bin", "is_deleted")
    def _compute_is_in_recycle_bin(self):
        for record in self:
            record.is_in_recycle_bin = bool(
                record.is_deleted or (record.directory_id and record.directory_id.is_recycle_bin)
            )

    def _update_content_vals(self, vals, binary):
        new_vals = vals.copy()
        new_vals.update(
            {
                "checksum": self._get_checksum(binary),
                "size": binary and len(binary) or 0,
            }
        )
        if self.storage_id.save_type in ["file", "attachment"]:
            new_vals["content_file"] = self.content
        else:
            if not binary:
                new_vals["content_binary"] = False
            elif isinstance(self.content, str) and self.content:
                new_vals["content_binary"] = self.content
            else:
                import base64
                new_vals["content_binary"] = base64.b64encode(binary).decode("ascii")
        return new_vals

    @api.depends("content_binary", "content_file", "attachment_id")
    def _compute_content(self):
        bin_size = self.env.context.get("bin_size", False)
        for record in self:
            if record.content_file:
                context = {"human_size": True} if bin_size else {"base64": True}
                record.content = record.with_context(**context).content_file
            elif record.content_binary:
                record.content = record.content_binary
            elif record.attachment_id:
                context = {"human_size": True} if bin_size else {"base64": True}
                record.content = record.with_context(**context).attachment_id.datas

    @api.model
    def _get_binary_max_size(self):
        icp = self.env["ir.config_parameter"].sudo()
        if hasattr(icp, "get_int"):
            return icp.get_int("dms.binary_max_size", default=500)
        getter = getattr(icp, "get_str", getattr(icp, "get_param", lambda k, default: 500))
        return int(getter("dms.binary_max_size", default=500))

    def action_toggle_downloadable(self):
        for record in self:
            if not record.can_manage_download:
                raise AccessError(_("Bạn không có quyền thay đổi cấu hình cho phép tải về của tệp tin này! Chỉ Trưởng phòng/Quản lý hoặc Quản trị viên mới có quyền này."))
            record.is_downloadable = not record.is_downloadable

    def action_download_file(self):
        self.ensure_one()
        if not self.can_download:
            raise AccessError(_("Bạn không có quyền tải tệp này về máy!"))
        return {
            "type": "ir.actions.act_url",
            "url": f"/dms/file/{self.id}/download",
            "target": "new",
        }

    def action_bulk_allow_download(self):
        for record in self:
            if not record.can_manage_download:
                raise AccessError(_("Bạn không có quyền quản lý cấu hình tải về cho file '%s'!") % record.name)
        self.write({"is_downloadable": True})

    def action_bulk_disallow_download(self):
        for record in self:
            if not record.can_manage_download:
                raise AccessError(_("Bạn không có quyền quản lý cấu hình tải về cho file '%s'!") % record.name)
        self.write({"is_downloadable": False})

    def _get_raw_content(self):
        """Safely extract raw binary bytes from content (BinaryValue / bytes / base64)."""
        self.ensure_one()
        val = self.content
        if not val:
            return b""
        if hasattr(val, "content"):
            return val.content
        if isinstance(val, (bytes, bytearray)):
            return bytes(val)
        if isinstance(val, str):
            try:
                return base64.b64decode(val.encode("ascii"))
            except Exception:
                return val.encode("utf-8")
        try:
            return bytes(val)
        except Exception:
            return b""

    def _is_excel_spreadsheet(self):
        """Check if file is an Excel spreadsheet or tabular data format."""
        self.ensure_one()
        ext = (self.extension or "").lower()
        if ext in EXCEL_EXTENSIONS:
            return True
        mimetype = (self.mimetype or "").lower()
        if any(keyword in mimetype for keyword in [
            "spreadsheet", "ms-excel", "csv", "tab-separated"
        ]):
            return True
        return False

    def _is_office_document(self):
        """Check if file is a Microsoft Office or OpenOffice document format."""
        self.ensure_one()
        ext = (self.extension or "").lower()
        if ext in OFFICE_EXTENSIONS:
            return True
        mimetype = (self.mimetype or "").lower()
        if any(keyword in mimetype for keyword in [
            "officedocument", "msword", "ms-excel", "ms-powerpoint", "opendocument", "rtf"
        ]):
            return True
        return False

    @api.model
    def _find_soffice_executable(self):
        import shutil
        soffice = shutil.which("soffice")
        if soffice:
            return soffice
        candidates = [
            r"C:\Program Files\LibreOffice\program\soffice.exe",
            r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
            os.path.expandvars(r"%ProgramFiles%\LibreOffice\program\soffice.exe"),
            os.path.expandvars(r"%ProgramFiles(x86)%\LibreOffice\program\soffice.exe"),
            os.path.expandvars(r"%LOCALAPPDATA%\Programs\LibreOffice\program\soffice.exe"),
        ]
        for c in candidates:
            if c and os.path.isfile(c):
                return c
        return None

    def _convert_to_pdf(self):
        """Convert office/document content to PDF using headless LibreOffice."""
        self.ensure_one()
        soffice_bin = self._find_soffice_executable()
        if not soffice_bin:
            return None
        raw = self._get_raw_content()
        if not raw:
            return None

        try:
            os.makedirs(PDF_PREVIEW_DIR, exist_ok=True)
        except Exception as e:
            _logger.warning("Failed to create PDF cache dir %s: %s", PDF_PREVIEW_DIR, e)

        checksum = self.checksum or hashlib.sha1(raw).hexdigest()
        cache_path = os.path.join(PDF_PREVIEW_DIR, f"{self.id}_{checksum}.pdf")

        if os.path.isfile(cache_path) and os.path.getsize(cache_path) > 0:
            try:
                with open(cache_path, "rb") as f:
                    return f.read()
            except Exception as e:
                _logger.warning("Failed to read cached PDF for file %s: %s", self.id, e)

        ext = (self.extension or "bin").lower()
        with tempfile.TemporaryDirectory() as temp_dir:
            input_file = os.path.join(temp_dir, f"document.{ext}")
            try:
                with open(input_file, "wb") as f:
                    f.write(raw)
            except Exception as e:
                _logger.error("Failed to write temporary file for conversion: %s", e)
                return None

            import pathlib
            profile_dir = os.path.join(temp_dir, "profile")
            profile_uri = pathlib.Path(profile_dir).as_uri()
            soffice_bin = self._find_soffice_executable()
            cmd = [
                soffice_bin,
                "--headless",
                f"-env:UserInstallation={profile_uri}",
                "--convert-to",
                "pdf",
                "--outdir",
                temp_dir,
                input_file,
            ]
            try:
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    timeout=30,
                    check=False,
                )
                if result.returncode != 0:
                    _logger.warning("LibreOffice conversion failed: %s", result.stderr)
                    return None
                pdf_output = os.path.join(temp_dir, "document.pdf")
                if os.path.isfile(pdf_output):
                    with open(pdf_output, "rb") as f:
                        pdf_data = f.read()
                    try:
                        with open(cache_path, "wb") as f:
                            f.write(pdf_data)
                    except Exception as e:
                        _logger.warning("Failed to write PDF cache %s: %s", cache_path, e)
                    return pdf_data
            except Exception as e:
                _logger.error("Error executing LibreOffice conversion: %s", e)
                return None
        return None

    def _compute_preview_html(self):
        for record in self:
            if not record.id:
                record.preview_html = False
                continue
            raw = record._get_raw_content()
            if not raw:
                record.preview_html = (
                    '<div class="d-flex flex-column align-items-center justify-content-center h-100 text-center p-5 text-muted" style="min-height: 60vh;">'
                    '<i class="fa fa-file-o fa-5x mb-3 text-secondary"></i>'
                    '<h4>Tệp này chưa có dữ liệu nội dung.</h4>'
                    '</div>'
                )
                continue

            mimetype = (record.mimetype or "").lower()
            ext = (record.extension or "").lower()
            ts = int(fields.Datetime.now().timestamp())
            preview_url = f"/dms/file/{record.id}/preview?v={ts}"
            download_url = f"/dms/file/{record.id}/download"
            pdf_preview_url = preview_url if record.can_download else f"{preview_url}#toolbar=0&navpanes=0"

            # 1. PDF
            if mimetype == "application/pdf" or ext == "pdf":
                record.preview_html = (
                    f'<iframe src="{pdf_preview_url}" '
                    f'style="width: 100%; height: 86vh; border: none; display: block; background: #525659;"></iframe>'
                )
            # 2. Bảng tính Excel / CSV / ODS (Spreadsheet)
            elif record._is_excel_spreadsheet():
                record.preview_html = (
                    f'<iframe src="{preview_url}" '
                    f'style="width: 100%; height: 86vh; border: none; display: block; background: #ffffff;"></iframe>'
                )
            # 3. PowerPoint (.pptx, .ppt, .odp)
            elif ext in ("pptx", "ppt", "odp", "ppsx"):
                record.preview_html = (
                    f'<iframe src="{preview_url}" '
                    f'style="width: 100%; height: 86vh; border: none; display: block; background: #14171a;"></iframe>'
                )
            # 4. Word (.docx, .doc, .odt, .rtf)
            elif ext in ("docx", "doc", "odt", "rtf"):
                record.preview_html = (
                    f'<iframe src="{preview_url}" '
                    f'style="width: 100%; height: 86vh; border: none; display: block; background: #e2e8f0;"></iframe>'
                )
            # 5. Tài liệu văn phòng khác -> Preview qua PDF chuyển đổi
            elif record._is_office_document():
                office_pdf_url = f"/dms/file/{record.id}/preview?as_pdf=1&v={ts}"
                if not record.can_download:
                    office_pdf_url = f"{office_pdf_url}#toolbar=0&navpanes=0"
                record.preview_html = (
                    f'<iframe src="{office_pdf_url}" '
                    f'style="width: 100%; height: 86vh; border: none; display: block; background: #525659;"></iframe>'
                )
            # 6. Hình ảnh (Image)
            elif mimetype.startswith("image/") or ext in [
                "png", "jpg", "jpeg", "gif", "svg", "webp", "bmp", "ico", "tiff", "tif"
            ]:
                record.preview_html = (
                    f'<div class="d-flex align-items-center justify-content-center h-100 p-3" '
                    f'style="min-height: 84vh; background: #1e1e1e;">'
                    f'<img src="{preview_url}" '
                    f'style="max-width: 100%; max-height: 84vh; object-fit: contain; '
                    f'box-shadow: 0 4px 20px rgba(0,0,0,0.5); border-radius: 6px; background: #fff;" '
                    f'alt="{record.name}"/>'
                    f'</div>'
                )
            # 7. Video
            elif mimetype.startswith("video/") or ext in [
                "mp4", "webm", "mkv", "mov", "ogg", "avi", "m4v", "wmv", "3gp", "flv"
            ]:
                record.preview_html = (
                    f'<iframe src="{preview_url}" '
                    f'style="width: 100%; height: 86vh; border: none; display: block; background: #000000;" '
                    f'allow="autoplay; fullscreen; picture-in-picture"></iframe>'
                )
            # 8. Âm thanh (Audio)
            elif mimetype.startswith("audio/") or ext in ["mp3", "wav", "ogg", "aac", "flac", "m4a"]:
                record.preview_html = (
                    f'<iframe src="{preview_url}" '
                    f'style="width: 100%; height: 86vh; border: none; display: block; background: #0f111a;" '
                    f'allow="autoplay"></iframe>'
                )
            # 9. Văn bản thô (Text / Code / Log)
            elif (
                mimetype.startswith("text/")
                or ext in ["txt", "log", "json", "xml", "csv", "md", "py", "js", "html", "css", "sql", "sh", "bat", "ini", "conf"]
            ):
                try:
                    text_content = raw.decode("utf-8")
                except UnicodeDecodeError:
                    try:
                        text_content = raw.decode("latin1")
                    except Exception:
                        text_content = str(raw)
                import html
                escaped_text = html.escape(text_content[:200000])
                record.preview_html = (
                    f'<div style="height: 86vh; overflow: auto; background: #282c34; color: #abb2bf; padding: 20px; font-family: monospace; font-size: 13px; line-height: 1.6; white-space: pre-wrap; word-break: break-all;">'
                    f'{escaped_text}'
                    f'</div>'
                )
            # 10. Tệp không hỗ trợ xem trực tiếp
            else:
                action_ui = (
                    f'<a href="{download_url}" class="btn btn-primary btn-lg shadow-sm"><i class="fa fa-download me-2"></i>Tải tệp về máy</a>'
                    if record.can_download
                    else '<div class="alert alert-warning d-inline-block px-4 py-2 mt-2"><i class="fa fa-lock me-2"></i>Tệp này hiện đang bị khóa tải về. Vui lòng liên hệ quản lý.</div>'
                )
                record.preview_html = (
                    f'<div class="d-flex flex-column align-items-center justify-content-center h-100 text-center p-5" '
                    f'style="min-height: 70vh; background: #f8f9fa;">'
                    f'<i class="fa fa-file-archive-o fa-5x text-secondary mb-4"></i>'
                    f'<h3 class="fw-bold mb-2">{record.name}</h3>'
                    f'<p class="text-muted mb-1">Định dạng: <strong>{mimetype or ext.upper()}</strong> ({record.human_size or ""})</p>'
                    f'<p class="text-muted mb-4">Định dạng tệp này không hỗ trợ xem trực tiếp trên trình duyệt.</p>'
                    f'{action_ui}'
                    f'</div>'
                )

    @api.model
    def search_panel_select_range(self, field_name, **kwargs):
        """Đảm bảo Searchpanel bên trái phân hệ Tài liệu luôn hiển thị đầy đủ danh sách thư mục,
        ngay cả khi thư mục đó chưa có file nào."""
        if field_name != "directory_id":
            context = {}
            if field_name == "category_id":
                context["category_short_name"] = True
            return super(DmsFile, self.with_context(**context)).search_panel_select_range(field_name, **kwargs)

        domain = [("is_hidden", "=", False)]
        if self.env.context.get("active_model") == "dms.directory":
            active_id = self.env.context.get("active_id")
            files = self.env["dms.file"].search([("directory_id", "child_of", active_id)])
            all_directory_ids = []
            for file_record in files:
                directory = file_record.directory_id
                while directory:
                    all_directory_ids.append(directory.id)
                    directory = directory.parent_id
            domain.append(("id", "in", all_directory_ids))

        comodel_records = (
            self.env["dms.directory"]
            .with_context(directory_short_name=True)
            .search_read(domain, ["display_name", "parent_id"])
        )
        all_record_ids = [rec["id"] for rec in comodel_records]
        field_range = {}
        enable_counters = kwargs.get("enable_counters")
        for record in comodel_records:
            record_id = record["id"]
            parent = record["parent_id"]
            record_values = {
                "id": record_id,
                "display_name": record["display_name"],
                "parent_id": parent[0] if parent and parent[0] in all_record_ids else False,
            }
            if enable_counters:
                record_values["__count"] = 0
            field_range[record_id] = record_values
        if enable_counters:
            res = super().search_panel_select_range(field_name, **kwargs)
            for item in res.get("values", []):
                if item["id"] in field_range:
                    field_range[item["id"]]["__count"] = item["__count"]
        return {"parent_field": "parent_id", "values": list(field_range.values())}

    def action_preview_file(self):
        self.ensure_one()
        view_id = self.env.ref("tdb_dms_custom.view_dms_file_preview_modal", raise_if_not_found=False)
        return {
            "name": self.name or _("Xem trước tài liệu"),
            "type": "ir.actions.act_window",
            "res_model": "dms.file",
            "res_id": self.id,
            "view_mode": "form",
            "views": [(view_id.id if view_id else False, "form")],
            "target": "new",
            "context": dict(self.env.context, form_view_initial_mode="view"),
        }

    @api.model
    def _search(self, domain, offset=0, limit=None, order=None, **kwargs):
        """Người dùng thông thường không bao giờ nhìn thấy các file trong Thùng Rác hoặc đã bị xóa."""
        if not self.env.su:
            user = self.env.user
            is_admin = user._is_superuser() or user.has_group("base.group_system")
            if not is_admin:
                domain = expression.AND([
                    domain,
                    [("is_deleted", "=", False), ("directory_id.is_recycle_bin", "!=", True)]
                ])
        return super()._search(domain, offset=offset, limit=limit, order=order, **kwargs)

    def unlink(self):
        """Cơ chế Thùng Rác (Soft Delete):
        - Khi xóa file ở bất kỳ phòng ban nào, file được chuyển vào Thùng Rác, lưu người xóa và thời gian xóa.
        - Chỉ khi file đã nằm trong Thùng Rác và do Sếp/Admin xóa thì mới xóa vĩnh viễn khỏi CSDL.
        """
        user = self.env.user
        is_admin = self.env.su or user._is_superuser() or user.has_group("base.group_system")

        recycle_bin_dir = self.env["dms.directory"].get_recycle_bin()

        files_to_hard_delete = self.env["dms.file"]
        for record in self:
            # Nếu file đã nằm trong Thùng rác
            if record.directory_id.id == recycle_bin_dir.id or record.is_deleted:
                if is_admin:
                    files_to_hard_delete |= record
                else:
                    raise AccessError(_("Chỉ Quản trị viên (Sếp/Admin) mới có quyền xóa vĩnh viễn tệp tin trong Thùng Rác!"))
            else:
                # Kiểm tra quyền xóa thông thường trên file
                record.check_access("unlink")
                # Chuyển vào Thùng Rác (Soft Delete)
                record.sudo().write({
                    "original_directory_id": record.directory_id.id,
                    "deleted_uid": user.id,
                    "deleted_date": fields.Datetime.now(),
                    "is_deleted": True,
                    "directory_id": recycle_bin_dir.id,
                })
                record.sudo().message_post(
                    body=_("Tệp tin đã được chuyển vào Thùng Rác bởi người dùng <b>%s</b>.") % user.name,
                    subtype_xmlid="mail.mt_note",
                )

        if files_to_hard_delete:
            return super(DmsFile, files_to_hard_delete).unlink()
        return True

    def action_restore_file(self):
        """Khôi phục tệp tin từ Thùng Rác về lại thư mục ban đầu."""
        user = self.env.user
        is_admin = self.env.su or user._is_superuser() or user.has_group("base.group_system")
        if not is_admin:
            raise AccessError(_("Chỉ Quản trị viên (Sếp/Admin) mới có quyền khôi phục tệp tin từ Thùng Rác!"))

        restored_count = 0
        for record in self:
            target_dir = record.original_directory_id
            if not target_dir or not target_dir.exists():
                target_dir = self.env["dms.directory"].sudo().search(
                    [("is_sharing", "=", True)], limit=1
                )
                if not target_dir:
                    target_dir = self.env["dms.directory"].sudo().search(
                        [("is_root_directory", "=", True), ("is_recycle_bin", "=", False)], limit=1
                    )

            if target_dir:
                record.sudo().write({
                    "directory_id": target_dir.id,
                    "is_deleted": False,
                    "deleted_uid": False,
                    "deleted_date": False,
                })
                record.sudo().message_post(
                    body=_("Tệp tin đã được khôi phục về thư mục <b>%s</b> bởi <b>%s</b>.") % (target_dir.name, user.name),
                    subtype_xmlid="mail.mt_note",
                )
                restored_count += 1

        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Khôi phục thành công"),
                "message": _("Đã khôi phục %s tệp tin về thư mục ban đầu.") % restored_count,
                "type": "success",
                "sticky": False,
                "next": {"type": "ir.actions.client", "tag": "reload"},
            },
        }

    def action_permanent_delete(self):
        """Xóa vĩnh viễn tệp tin khỏi CSDL dành cho Sếp/Admin."""
        user = self.env.user
        is_admin = self.env.su or user._is_superuser() or user.has_group("base.group_system")
        if not is_admin:
            raise AccessError(_("Chỉ Quản trị viên (Sếp/Admin) mới có quyền xóa vĩnh viễn!"))
        return super().unlink()

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("directory_id") and "is_downloadable" not in vals:
                directory = self.env["dms.directory"].browse(vals["directory_id"])
                if directory.exists():
                    vals["is_downloadable"] = directory.default_allow_download
        return super().create(vals_list)

