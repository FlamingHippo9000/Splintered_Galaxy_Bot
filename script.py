import time
import argparse
import logging
import pandas as pd
from unbelievaboat import Client
import requests
from dotenv import load_dotenv


# ----------------------------
# Setup
# ----------------------------
load_dotenv()

BOAT_API_BASE = dotenv_values(".env")("BOAT_API_BASE_URL")
BOAT_API_KEY = dotenv_values(".env")("BOAT_API_KEY")
SHEET_URL = dotenv_values(".env")("SHEET_URL")

if not BOAT_API_BASE or not BOAT_API_KEY or not SHEET_URL:
    raise ValueError("Missing required environment variables in .env")

logging.basicConfig(
    filename="sync.log",
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)


# ----------------------------
# Load Data
# ----------------------------
def load_sheet():
    try:
        df = pd.read_csv(SHEET_URL)
        return df
    except Exception as e:
        raise RuntimeError(f"Failed to load sheet: {e}")


# ----------------------------
# Clean + Normalize Data
# ----------------------------
def clean_data(df: pd.DataFrame) -> pd.DataFrame:
    df.columns = [col.strip().lower() for col in df.columns]

    required_cols = ["item_id", "name", "price", "active"]
    missing = [c for c in required_cols if c not in df.columns]
    if missing:
        raise ValueError(f"Missing required columns: {missing}")

    df["item_id"] = pd.to_numeric(df["item_id"], errors="coerce")
    df["price"] = pd.to_numeric(df["price"], errors="coerce")

    df["active"] = (
        df["active"]
        .astype(str)
        .str.strip()
        .str.upper()
        .isin(["TRUE", "1", "YES"])
    )

    df["name"] = df["name"].astype(str).str.strip()

    return df


# ----------------------------
# Validation
# ----------------------------
def validate(df: pd.DataFrame):
    errors = []

    for idx, row in df.iterrows():
        if pd.isna(row["item_id"]):
            errors.append(f"Row {idx}: missing item_id")

        if pd.isna(row["price"]) or row["price"] < 0:
            errors.append(f"Row {idx}: invalid price")

        if pd.isna(row["quantity"]) or row["quantity"] < 0:
            errors.append(f"Row {idx}: invalid quantity")

        if not row["name"]:
            errors.append(f"Row {idx}: missing name")

    if errors:
        for e in errors:
            print("❌", e)
        raise ValueError("Validation failed. Fix sheet before syncing.")


# ----------------------------
# API Call
# ----------------------------
def update_item(item: dict):
    url = f"{BOAT_API_BASE}/items/{int(item['item_id'])}"

    payload = {
        "name": item["name"],
        "price": float(item["price"]),
        "quantity": int(item["quantity"]),
        "active": bool(item["active"]),
    }

    headers = {
        "Authorization": f"Bearer {BOAT_API_KEY}",
        "Content-Type": "application/json",
    }

    response = requests.put(url, json=payload, headers=headers)
    return response


# ----------------------------
# Sync Logic
# ----------------------------
def sync(df: pd.DataFrame, dry_run: bool):
    items = df.to_dict(orient="records")

    success = 0
    failed = 0

    for item in items:
        if dry_run:
            print(f"[DRY RUN] Would update item {int(item['item_id'])}: {item}")
            continue

        try:
            res = update_item(item)

            if res.status_code in (200, 201):
                print(f"✅ Updated {int(item['item_id'])}")
                logging.info(f"Updated {item['item_id']}")
                success += 1
            else:
                print(f"❌ Failed {int(item['item_id'])}: {res.text}")
                logging.error(f"Failed {item['item_id']}: {res.text}")
                failed += 1

        except Exception as e:
            print(f"🔥 Error {int(item['item_id'])}: {e}")
            logging.exception(f"Exception for {item['item_id']}")
            failed += 1

        time.sleep(0.1)  # basic rate limiting

    print("\n--- Summary ---")
    print(f"Success: {success}")
    print(f"Failed: {failed}")


# ----------------------------
# Main
# ----------------------------
def main():
    parser = argparse.ArgumentParser(description="Sync inventory from Google Sheets to API")
    parser.add_argument("--dry-run", action="store_true", help="Preview changes without sending API calls")

    args = parser.parse_args()

    print("Loading sheet...")
    df = load_sheet()

    print("Cleaning data...")
    df = clean_data(df)

    print("Validating data...")
    validate(df)

    print("Starting sync...")
    sync(df, dry_run=args.dry_run)


if __name__ == "__main__":
    main()