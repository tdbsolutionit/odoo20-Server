"""Database discovery for the Omnia CAD XML-RPC client."""

from xml.parsers.expat import ExpatError
from xmlrpc.client import Fault, ResponseError, dumps, loads

from odoo import http
from odoo.http import request
from odoo.http.router import db_filter, db_list
from odoo.tools import config


class PLMDatabaseRPC(http.Controller):
    @http.route(
        '/xmlrpc/db',
        type='http',
        auth='none',
        methods=['POST'],
        csrf=False,
        save_session=False,
    )
    def xmlrpc_db(self):
        """Expose databases allowed by the Odoo configuration."""
        try:
            _params, method = loads(request.httprequest.get_data())
        except (ExpatError, ResponseError, ValueError, TypeError):
            result = Fault(-32700, 'Invalid XML-RPC request')
        else:
            if method in ('list', 'list_db'):
                # CAD needs discovery even with database management disabled.
                # Expose only explicitly configured databases in that case;
                # do not enumerate PostgreSQL or enable global list_db.
                databases = config['db_name']
                result = (db_filter(databases) if databases else db_list(),)
            else:
                result = Fault(-32601, 'Only list and list_db are supported')

        return request.make_response(
            dumps(result, methodresponse=True, encoding='utf-8'),
            headers=[('Content-Type', 'text/xml; charset=utf-8')],
        )
