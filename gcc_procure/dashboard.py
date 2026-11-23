"""Streamlit dashboard:  python procure.py dashboard   (or: streamlit run gcc_procure/dashboard.py)"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
import streamlit as st

from gcc_procure import config, pipeline, queries
from gcc_procure.db import DB
from gcc_procure.exporter import build_frames, to_xlsx_bytes
from gcc_procure.importer import manual_research_queue

st.set_page_config(page_title="GCC Retail Procurement Intelligence", layout="wide", page_icon="🛒")


@st.cache_resource
def get_db():
    return DB()


db = get_db()
df = queries.leads_df(db)
st.title("GCC Retail Procurement Intelligence")
st.caption("AWS Distribution UAE · publicly available business information only · every row carries its source URL")

if df.empty:
    st.info("Database is empty. Run `python procure.py init` then `python procure.py run --phase 1`.")
    st.stop()

# ---- sidebar filters ---------------------------------------------------------------------------
sb = st.sidebar
sb.header("Filters")


def pick(label, col):
    opts = sorted(x for x in df[col].dropna().unique() if x != "")
    return sb.multiselect(label, opts)


f_country, f_city, f_cat = pick("Country", "country"), pick("City / Emirate", "city"), pick("Business category", "business_category")
f_size = pick("Company size", "business_size")
f_desig = sb.multiselect("Job designation", sorted(x for x in df["designation"].dropna().unique() if x))
f_avail = sb.multiselect("Contact availability", ["Has email", "Has phone", "Named person", "No contact"])
f_score = sb.slider("Lead score", 0, 100, (0, 100))
f_ver = pick("Verification status", "verification_status")

v = df.copy()
for col, sel in (("country", f_country), ("city", f_city), ("business_category", f_cat), ("business_size", f_size),
                 ("designation", f_desig), ("verification_status", f_ver)):
    if sel:
        v = v[v[col].isin(sel)]
v = v[(v.lead_score >= f_score[0]) & (v.lead_score <= f_score[1])]
mask = pd.Series(False, index=v.index)
for a in f_avail:
    mask |= {"Has email": v.email.fillna("") != "", "Has phone": v.phone.fillna("") != "",
             "Named person": v.is_person, "No contact": ~v.has_contact}[a]
if f_avail:
    v = v[mask]

# ---- KPIs -------------------------------------------------------------------------------------------
k = queries.kpis(v)
cols = st.columns(4)
for i, (name, val) in enumerate(k.items()):
    cols[i % 4].metric(name, f"{val:,}")

tab_over, tab_leads, tab_dm, tab_manual, tab_prog, tab_export = st.tabs(
    ["Overview", "Leads", "Decision makers", "Manual research", "Progress", "Export"])

comp = v.drop_duplicates("company_id")
with tab_over:
    c1, c2 = st.columns(2)
    c1.subheader("Country-wise leads")
    c1.bar_chart(comp.groupby("country").size())
    c2.subheader("Category-wise leads")
    c2.bar_chart(comp.groupby("business_category").size())
    c3, c4 = st.columns(2)
    c3.subheader("Priority distribution")
    c3.bar_chart(comp.groupby("outreach_priority").size())
    c4.subheader("Top 15 companies by lead score")
    c4.dataframe(comp.nlargest(15, "lead_score")[["company_name", "country", "business_category", "lead_score", "outreach_priority"]],
                 hide_index=True, width='stretch')

with tab_leads:
    st.dataframe(queries.display(v), hide_index=True, width='stretch',
                 column_config={"Company Website": st.column_config.LinkColumn(), "Source URL": st.column_config.LinkColumn(),
                                "Supplier Registration URL": st.column_config.LinkColumn(),
                                "Contact Page URL": st.column_config.LinkColumn(), "LinkedIn Profile": st.column_config.LinkColumn()})

with tab_dm:
    dm = queries.decision_makers_df(v)
    st.caption("Verified = published by the company itself on its official website. "
               "'Discovered - Needs Review' and 'Imported - Unverified' must be checked before outreach.")
    st.dataframe(queries.display(dm), hide_index=True, width='stretch')

with tab_manual:
    q = pd.DataFrame(manual_research_queue(db))
    st.write(f"{len(q)} companies need manual contact research.")
    if not q.empty:
        st.dataframe(q, hide_index=True, width='stretch', column_config={
            "LinkedIn People Search (open manually)": st.column_config.LinkColumn(),
            "Web Search (open manually)": st.column_config.LinkColumn(), "Website": st.column_config.LinkColumn()})

with tab_prog:
    st.dataframe(pd.DataFrame(pipeline.phase_status(db)), hide_index=True, width='stretch')
    st.caption("Resume an interrupted phase by re-running: python procure.py run --phase N")

with tab_export:
    frames = build_frames(db)
    st.download_button("Download Excel (all sheets)", to_xlsx_bytes(frames), "gcc_procurement_leads.xlsx",
                       "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    for name, f in frames.items():
        st.download_button(f"CSV · {name} ({len(f)})", f.to_csv(index=False, encoding="utf-8").encode("utf-8-sig"),
                           f"{name.lower().replace(' ', '_').replace('&', 'and')}.csv", "text/csv", key=name)
    st.caption("Google Sheets: File → Import → Upload the .xlsx (all sheets) or any CSV.")
