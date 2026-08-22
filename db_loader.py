import os
import logging
import sqlite3
import pandas as pd

BASE_DIR = os.path.join(os.path.dirname(__file__), 'documents')
EXCEL_FILENAME = 'ParcelPilot_Assessment_Data.xlsx'


def load_excel_to_memory():
    """Load every sheet of the workbook into an in-memory SQLite DB.

    Returns (sheets, conn): sheets is {sheet_name: DataFrame}; each sheet is also a
    SQLite table of the same name (README, accounts, orders, tickets).
    """
    excel_path = os.path.join(BASE_DIR, EXCEL_FILENAME)
    sheets = pd.read_excel(excel_path, sheet_name=None)
    # check_same_thread=False: Streamlit serves requests from several threads.
    conn = sqlite3.connect(':memory:', check_same_thread=False)
    for name, df in sheets.items():
        df.to_sql(name, conn, index=False, if_exists='replace')
        logging.info(f"Sheet '{name}' -> SQLite table ({len(df)} rows)")
    return sheets, conn


def get_snapshot_time(sheets) -> str:
    """Reference time for all time-based reasoning, taken from the README sheet."""
    readme = sheets['README']
    row = readme[readme.iloc[:, 0] == 'Dataset snapshot']
    return str(row.iloc[0, 1]) if len(row) else 'unknown'
