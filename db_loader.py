import os
import logging
import pandas as pd
import sqlite3

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

BASE_DIR = os.path.join(os.path.dirname(__file__), 'documents')
EXCEL_FILENAME = 'ParcelPilot_Assessment_Data.xlsx'


def load_excel_to_memory():
    """Load every sheet of the Excel workbook into an in-memory SQLite DB.

    Returns (sheets, conn) where sheets is a dict of sheet_name -> DataFrame and
    each sheet is also available as a SQLite table of the same name
    (e.g. accounts, orders, tickets).
    """
    excel_path = os.path.join(BASE_DIR, EXCEL_FILENAME)
    try:
        sheets = pd.read_excel(excel_path, sheet_name=None)
        logging.info(f"Excel loaded successfully with sheets: {list(sheets)}.")
    except Exception as e:
        logging.error(f"Failed to load Excel file: {e}")
        raise

    try:
        conn = sqlite3.connect(':memory:')
        for name, df in sheets.items():
            df.to_sql(name, conn, index=False, if_exists='replace')
            logging.info(f"Sheet '{name}' saved as SQLite table with {len(df)} rows.")
    except Exception as e:
        logging.error(f"Failed to save Excel data to SQLite: {e}")
        raise

    return sheets, conn
