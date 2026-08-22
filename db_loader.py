import os
import logging
import pandas as pd
import sqlite3

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

BASE_DIR = os.path.join(os.path.dirname(__file__), 'AI Agent Assessment - Candidate Pack')
EXCEL_FILENAME = 'ParcelPilot_Assessment_Data.xlsx'


def load_excel_to_memory():
    """Load Excel data into Pandas DataFrame and SQLite in-memory DB."""
    excel_path = os.path.join(BASE_DIR, EXCEL_FILENAME)
    try:
        df = pd.read_excel(excel_path)
        logging.info(f"Excel loaded successfully with {len(df)} rows.")
    except Exception as e:
        logging.error(f"Failed to load Excel file: {e}")
        raise

    try:
        conn = sqlite3.connect(':memory:')
        df.to_sql('assessment_data', conn, index=False, if_exists='replace')
        logging.info("Excel data saved to in-memory SQLite database.")
    except Exception as e:
        logging.error(f"Failed to save Excel data to SQLite: {e}")
        raise

    return df, conn
