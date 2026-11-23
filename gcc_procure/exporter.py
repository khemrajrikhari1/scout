"""Excel / CSV / Google-Sheets-compatible exports (8 requested sheets + manual research queue)."""
from __future__ import annotations

import io
from pathlib import Path

import pandas as pd
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from . import config, queries
from .importer import manual_research_queue

SHEETS = [
    ("Supermarkets & Hypermarkets", lambda d: d[d.business_category == config.CAT_SUPER]),
    ("Food FMCG Buyers", lambda d: d[d.business_category == config.CAT_FOOD]),
    ("Non-Food FMCG Buyers", lambda d: d[d.business_category == config.CAT_NONFOOD]),
    ("Drug Stores & Pharmacies", lambda d: d[d.business_category == config.CAT_PHARMA]),
    ("FMCG Wholesalers", lambda d: d[d.business_category == config.CAT_WHOLESALE]),
    ("Distributors & Importers", lambda d: d[d.business_category == config.CAT_DIST]),
    ("Procurement Decision Makers", lambda d: queries.decision_makers_df(d)),
    ("High Priority Leads", lambda d: d[d.outreach_priority == "High Priority"]),
]


def build_frames(db, df: pd.DataFrame | None = None) -> dict[str, pd.DataFrame]:
    df = queries.leads_df(db) if df is None else df
    frames = {name: queries.display(fn(df)) for name, fn in SHEETS}
    manual = pd.DataFrame(manual_research_queue(db))
    frames["Manual Research Needed"] = manual
    return frames


def _style(ws):
    head_fill = PatternFill("solid", fgColor="1F3A5F")
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = head_fill
        cell.alignment = Alignment(vertical="center", wrap_text=True)
    ws.freeze_panes = "B2"
    if ws.max_row >= 1 and ws.max_column >= 1:
        ws.auto_filter.ref = ws.dimensions
    for i, col in enumerate(ws.columns, 1):
        width = max((len(str(c.value)) for c in list(col)[:200] if c.value is not None), default=8)
        ws.column_dimensions[get_column_letter(i)].width = min(max(width + 2, 12), 55)


def to_xlsx_bytes(frames: dict[str, pd.DataFrame]) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        for name, f in frames.items():
            f.to_excel(xw, sheet_name=name[:31], index=False)
            _style(xw.sheets[name[:31]])
    return buf.getvalue()


def export_all(db, out_dir: Path | None = None) -> dict:
    out_dir = Path(out_dir or config.EXPORT_DIR)
    (out_dir / "csv").mkdir(parents=True, exist_ok=True)
    (out_dir / "google_sheets").mkdir(parents=True, exist_ok=True)
    frames = build_frames(db)
    xlsx = out_dir / "gcc_procurement_leads.xlsx"
    xlsx.write_bytes(to_xlsx_bytes(frames))
    files = [xlsx]
    for name, f in frames.items():
        slug = name.lower().replace(" & ", "_and_").replace(" ", "_")
        p = out_dir / "csv" / f"{slug}.csv"
        f.to_csv(p, index=False, encoding="utf-8")
        g = out_dir / "google_sheets" / f"{slug}.csv"   # UTF-8 with BOM: opens cleanly in Excel & Google Sheets import
        f.to_csv(g, index=False, encoding="utf-8-sig")
        files += [p, g]
    return {"dir": str(out_dir), "files": [str(x) for x in files], "rows": {k: len(v) for k, v in frames.items()}}
