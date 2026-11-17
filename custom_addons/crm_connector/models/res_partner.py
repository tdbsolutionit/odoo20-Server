import logging
import os
import json

import requests

from odoo import api, fields, models
from odoo.exceptions import UserError
from pathlib import Path

_logger = logging.getLogger(__name__)


class ResPartner(models.Model):
    _inherit = 'res.partner'

    external_customer_id = fields.Char(
        string='External Customer ID',
        index=True,
        copy=False,
        readonly=True,
    )
    external_customer_code = fields.Char(
        string="External Customer Code"
    )

    external_short_code = fields.Char(
        string="External Short Code"
    )

    external_industry = fields.Char(
        string="External Industry"
    )

    external_status = fields.Char(
        string="External Status"
    )

    external_source_name = fields.Char(
        string="External Source"
    )

    external_opp_count = fields.Integer(
        string="External Opportunity Count"
    )

    external_open_opp_count = fields.Integer(
        string="External Open Opportunities"
    )

    external_created_at = fields.Datetime(
        string="External Creation Date"
    )

    @api.model
    def import_customers_from_json(self):

        file_path = (
            Path(__file__).resolve().parent.parent
            / "sample_data"
            / "response_1793421204104.json"
        )

        with open(file_path, "r", encoding="utf-8") as file:
            payload = json.load(file)

        customers = payload["data"]

        Partner = self.env["res.partner"]

        created = 0
        updated = 0

        for customer in customers:
            values = self._map_external_customer(customer)

            existing = Partner.search([
                (
                    "external_customer_id",
                    "=",
                    values["external_customer_id"]
                )
            ], limit=2)

            if len(existing) > 1:
                raise ValueError("Duplicate external customer ID")

            if existing:
                existing.write(values)
                updated += 1
            else:
                Partner.create(values)
                created += 1

        return {
            "total": len(customers),
            "created": created,
            "updated": updated,
        }

    @api.model
    def _map_external_customer(self, customer):
        from datetime import datetime, timezone

        values = {
            "external_customer_id": str(customer["id"]),
            "name": customer["name"],
            "is_company": customer.get("type") == "business",
            "external_customer_code": customer.get("code") or False,
            "external_short_code": customer.get("shortCode") or False,
            "external_industry": customer.get("industry") or False,
            "external_status": customer.get("status") or False,
            "external_source_name": customer.get("sourceName") or False,
            "external_opp_count": customer.get("oppCount") or 0,
            "external_open_opp_count": customer.get("openOppCount") or 0,
        }

        field_mapping = {
            "phone": "phone",
            "email": "email",
            "address": "street",
            "taxCode": "vat",
            "notes": "comment",
        }

        for source_field, odoo_field in field_mapping.items():
            if source_field in customer:
                values[odoo_field] = (
                    customer[source_field] or False
                )

        created_at = customer.get("createdAt")

        if created_at:
            dt = datetime.fromisoformat(
                created_at.replace("Z", "+00:00")
            )
            dt = dt.astimezone(timezone.utc)

            values["external_created_at"] = (
                dt.replace(tzinfo=None)
            )

        return values
    
    @api.model
    def sync_external_customers(self):
        customers = self._fetch_all_customers()

        Partner = self.env["res.partner"]

        created = 0
        updated = 0

        for customer in customers:
            values = self._map_external_customer(customer)

            external_id = values["external_customer_id"]

            existing = Partner.search([
                ("external_customer_id", "=", external_id)
            ], limit=2)

            if len(existing) > 1:
                raise UserError(
                    f"Duplicate external customer ID: {external_id}"
                )

            if existing:
                existing.write(values)
                updated += 1
            else:
                Partner.create(values)
                created += 1

        return {
            "total": len(customers),
            "created": created,
            "updated": updated,
        }

    @api.model
    def _get_external_access_token(self):
        base_url = os.environ.get(
            "EXTERNAL_CUSTOMER_API_BASE_URL", ""
        ).rstrip("/")

        email = os.environ.get("EXTERNAL_API_EMAIL")
        password = os.environ.get("EXTERNAL_API_PASSWORD")

        #if not base_url.startswith("https://"):
        #    raise UserError(
        #        "HTTPS is required for integration authentication."
        #    )

        if not email or not password:
            raise UserError(
                "External API email or password is missing."
            )

        response = requests.post(
            f"{base_url}/api/auth/integration/login",
            json={
                "email": email,
                "password": password,
            },
            timeout=15,
            allow_redirects=False,
        )

        response.raise_for_status()

        result = response.json()

        # Assumes the API returns {"accessToken": "..."}
        token = result.get("accessToken")

        if not isinstance(token, str) or not token:
            raise UserError(
                "Login succeeded but no access token was found."
            )

        return token

    @api.model
    def _fetch_all_customers(self):
        base_url = os.environ.get(
            "EXTERNAL_CUSTOMER_API_BASE_URL", ""
        ).rstrip("/")

        token = os.environ.get(
            "EXTERNAL_CUSTOMER_API_TOKEN", ""
        )

        #if not base_url.startswith("https://"):
        #    raise UserError(
        #        "A secure HTTPS API endpoint is required."
        #    )

        if not token:
            raise UserError(
                "External API token is missing."
            )

        all_customers = []
        seen_ids = set()
        page = 1

        with requests.Session() as session:
            session.headers.update({
                "Accept": "application/json",
                "Authorization": f"Bearer {token}",
            })

            while True:
                response = session.get(
                    f"{base_url}/api/customers",
                    params={
                        "page": page,
                        "limit": 20,
                    },
                    timeout=20,
                    allow_redirects=False,
                )

                response.raise_for_status()
                payload = response.json()

                customers = payload["data"]
                meta = payload["meta"]

                if not isinstance(customers, list):
                    raise UserError(
                        "Invalid API customer list."
                    )

                if int(meta["page"]) != page:
                    raise UserError(
                        "Unexpected page returned by API."
                    )

                total_pages = int(meta["totalPages"])

                if total_pages < 1 or total_pages > 100:
                    raise UserError(
                        "Invalid API pagination."
                    )

                for customer in customers:
                    external_id = str(customer["id"])

                    if external_id in seen_ids:
                        raise UserError(
                            "Duplicate customer ID across pages."
                        )

                    seen_ids.add(external_id)
                    all_customers.append(customer)

                if page >= total_pages:
                    break

                page += 1

        return all_customers
