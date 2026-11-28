"""Keep legacy CAD user pickers compatible with Odoo 20 binary values."""
import threading

from lxml import etree

from odoo import api, models, _
from odoo.http import request


class ResUsers(models.Model):
    _inherit = 'res.users'

    def name_get(self):
        """Legacy CAD selection widgets still resolve selected IDs this way."""
        self.check_access('read')
        return [(row['id'], row['display_name']) for row in self.read(['display_name'])]

    @api.model
    def koo_fields_view_get(self, view_id=None, view_type='form', toolbar=False, submenu=False):
        result = super().koo_fields_view_get(view_id, view_type, toolbar, submenu)
        if not self.env.context.get('odooPLM') or view_type not in ('list', 'tree'):
            return result
        # The CAD grid needs an ID and readable labels, not the administrative
        # user list with Odoo 20 binary/avatar widgets. Keep the original view
        # result contract and root tag that Omnia expects.
        root = etree.fromstring(result['arch'])
        selector = etree.Element(root.tag, string='Users', create='false', edit='false', delete='false')
        etree.SubElement(selector, 'field', name='name', string='Name', readonly='1')
        etree.SubElement(selector, 'field', name='login', string='Login', readonly='1')
        etree.SubElement(selector, 'field', name='company_id', string='Company', readonly='1')
        result['arch'] = etree.tostring(selector, encoding='unicode')
        result['fields'] = self.fields_get(['name', 'login', 'company_id'])
        result.pop('toolbar', None)
        return result

    @api.model
    def search_read(self, domain=None, fields=None, offset=0, limit=None, order=None, **read_kwargs):
        rpc_method = getattr(threading.current_thread(), 'rpc_model_method', None)
        if request or rpc_method != 'res.users.search_read' or read_kwargs.get('load') == 'web':
            return super().search_read(
                domain=domain, fields=fields, offset=offset, limit=limit,
                order=order, **read_kwargs,
            )
        # Odoo 20 search_read bypasses read(), so route this legacy path through
        # the adapter explicitly. Search and read retain the caller's access.
        records = self.search(domain or [], offset=offset, limit=limit, order=order)
        if 'active_test' in records.env.context:
            context = dict(records.env.context)
            context.pop('active_test')
            records = records.with_context(context)
        return records.read(fields=fields, **read_kwargs)

    def read(self, fields=None, load='_classic_read', *args, **kwargs):
        rpc_method = getattr(threading.current_thread(), 'rpc_model_method', None)
        legacy_rpc = not request and load != 'web' and rpc_method in (
            'res.users.read', 'res.users.search_read',
        )
        if not legacy_rpc:
            return super().read(fields=fields, load=load, *args, **kwargs)

        # Some CAD pickers flatten a binary dictionary from an earlier response
        # into columns such as avatar_128.size. These are metadata, not fields.
        # Read the actual binary through the normal ORM access checks first.
        requested = list(fields) if fields is not None else None
        aliases = {}
        for name in requested or []:
            base, separator, prop = name.partition('.')
            field = self._fields.get(base)
            if separator and field and field.type == 'binary' and prop in (
                'size', 'content', 'filename',
            ):
                aliases[name] = (base, prop)
        actual_fields = None if requested is None else list(dict.fromkeys(
            aliases[name][0] if name in aliases else name for name in requested
        ))
        rows = super().read(fields=actual_fields, load=load, *args, **kwargs)
        for row in rows:
            for alias, (base, prop) in aliases.items():
                value = row.get(base)
                row[alias] = value.get(prop, False) if isinstance(value, dict) else (
                    0 if prop == 'size' else False
                )
            for name, value in list(row.items()):
                field = self._fields.get(name)
                if field and field.type == 'binary' and isinstance(value, dict):
                    row[name] = value.get('content', False)
            for base, _prop in aliases.values():
                if base not in requested:
                    row.pop(base, None)
        return rows

    def _format_activity_group(self, model_name, activities):
        group = super()._format_activity_group(model_name, activities)
        group['total_count'] += group['planned_count']
        return group

    def _divide_activities_in_groups(self, activities):
        plm = activities.filtered(lambda activity: activity.tdb_plm_kind
            or activity.activity_type_id.change_activity_type)
        ordinary = activities - plm
        groups = super()._divide_activities_in_groups(ordinary)
        self._tdb_expand_activity_groups(groups, ordinary)
        # Keep core record access/company checks before including multiple tasks
        # attached to the same record in the clock.
        allowed_groups = super()._divide_activities_in_groups(plm)
        self._tdb_expand_activity_groups(allowed_groups, plm)
        allowed_ids = [activity_id for group in allowed_groups
                       if group['model'] != 'mail.activity'
                       for activity_id in group['activity_ids']]
        visible = plm.filtered(lambda activity: activity.id in allowed_ids)
        if visible:
            group = self._format_activity_group('mail.activity', visible)
            group.update({
                'id': 'tdb_plm.activities', 'name': _('PLM Activities'),
                'icon': '/plm/static/description/icon.png',
                'tdb_plm_action_id': self.env.ref('activity_validation.plm_action_view_activities').id,
            })
            groups.append(group)
        return groups

    def _tdb_expand_activity_groups(self, groups, activities):
        def record_key(activity):
            return (activity.res_model, activity.res_id) if activity.res_id else (False, activity.id)

        by_record = activities.grouped(record_key)
        Activity = self.env['mail.activity']
        for group in groups:
            representatives = Activity.browse(group['activity_ids'])
            group_activities = Activity.union(by_record[record_key(a)] for a in representatives)
            formatted = self._format_activity_group(group['model'], group_activities)
            group.update({key: formatted[key] for key in (
                'total_count', 'today_count', 'overdue_count', 'planned_count', 'activity_ids',
            )})
