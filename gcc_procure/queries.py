"""Shared read-side queries used by the exporter and the dashboard."""
from __future__ import annotations

import pandas as pd

from . import config

LEAD_COLUMNS = [  # (db alias, header) in the order requested in the brief
    ("company_name", "Company Name"), ("parent_company", "Parent Company"), ("country", "Country"), ("city", "Emirate / City"),
    ("business_category", "Business Category"), ("business_subcategory", "Business Subcategory"), ("website", "Company Website"),
    ("branches", "Number of Branches"), ("business_size", "Business Size"), ("trade_type", "Retailer / Wholesaler / Distributor"),
    ("contact_name", "Contact Person Name"), ("designation", "Job Designation"), ("department", "Department"),
    ("linkedin_url", "LinkedIn Profile"), ("email", "Professional Email"), ("phone", "Business Phone Number"),
    ("contact_source", "Contact Source"), ("verification_status", "Verification Status"),
    ("product_categories", "Relevant Product Category"), ("placement_opportunity", "Potential Product Placement Opportunity"),
    ("supplier_registration_url", "Supplier Registration URL"), ("procurement_portal_url", "Procurement Portal URL"),
    ("contact_page_url", "Contact Page URL"), ("outreach_priority", "Suggested Outreach Priority"), ("research_notes", "Research Notes"),
    ("contact_source_url", "Source URL"), ("contact_date_collected", "Date Collected"), ("contact_last_verified", "Last Verified Date"),
    ("email_status", "Email Verification Status"), ("phone_status", "Phone Verification Status"),
    ("duplicate_status", "Duplicate Status"), ("confidence", "Confidence Score"), ("lead_score", "Lead Score"),
    ("company_source_url", "Company Source URL"), ("company_id", "Company ID"),
]

SQL = """
SELECT c.id AS company_id, c.company_name, c.parent_company, c.country, c.city, c.business_category, c.business_subcategory,
       c.website, c.website_status, c.branches, c.business_size, c.trade_type, c.product_categories, c.placement_opportunity,
       c.supplier_registration_url, c.procurement_portal_url, c.contact_page_url, c.outreach_priority, c.research_notes,
       c.lead_score, c.needs_manual, c.linkedin_company_url, c.source_url AS company_source_url,
       k.id AS contact_id, k.kind, k.contact_name, k.designation, k.designation_tier, k.department, k.linkedin_url, k.email,
       k.email_role, k.email_status, k.phone, k.phone_status, k.contact_source, k.source_url AS contact_source_url,
       k.verification_status, k.date_collected AS contact_date_collected, k.last_verified AS contact_last_verified,
       COALESCE(k.duplicate_status, c.duplicate_status) AS duplicate_status,
       COALESCE(k.confidence, c.confidence) AS confidence
FROM companies c LEFT JOIN contacts k ON k.company_id = c.id
ORDER BY c.lead_score DESC, c.company_name, k.designation_tier DESC
"""


def leads_df(db) -> pd.DataFrame:
    df = pd.read_sql_query(SQL, db.conn)
    df["has_contact"] = df["contact_id"].notna() & ((df["email"].fillna("") != "") | (df["phone"].fillna("") != "") | (df["contact_name"].fillna("") != ""))
    df["is_person"] = df["kind"].eq("person")
    df["is_decision_maker"] = df["is_person"] | df["email_role"].eq("procurement")
    return df


def decision_makers_df(df: pd.DataFrame) -> pd.DataFrame:
    return df[df["is_decision_maker"]]


def display(df: pd.DataFrame) -> pd.DataFrame:
    cols = [(a, h) for a, h in LEAD_COLUMNS if a in df.columns]
    out = df[[a for a, _ in cols]].copy()
    out.columns = [h for _, h in cols]
    return out


def kpis(df: pd.DataFrame) -> dict:
    comp = df.drop_duplicates("company_id")
    has_c = df[df["has_contact"]].groupby("company_id").size()
    return {
        "Total Companies Discovered": int(comp.shape[0]),
        "Total Procurement Contacts": int(df["is_decision_maker"].sum()),
        "Verified Business Emails": int((df["email_status"] == "published_official").sum()),
        "Public Business Phone Numbers": int((df["phone_status"] == "published_official").sum()),
        "High-Priority Leads": int((comp["outreach_priority"] == "High Priority").sum()),
        "Companies Without Contacts": int((~comp["company_id"].isin(has_c.index)).sum()),
        "Supplier Registration Opportunities": int(((comp["supplier_registration_url"].fillna("") != "") |
                                                    (comp["procurement_portal_url"].fillna("") != "")).sum()),
    }
