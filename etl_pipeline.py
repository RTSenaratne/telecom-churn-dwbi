"""
ETL Pipeline: Telecom Customer Churn Data Warehouse
=====================================================
Extracts data from three source systems, transforms it (cleaning,
standardisation, derived attributes), and loads it into a star-schema
data warehouse (SQLite).

Sources:
  1. customer_billing_source.csv   - Customer & Billing System (CSV)
  2. location_reference_source.csv - Geographic Reference System (CSV)
  3. service_subscription_source.json - Service Provisioning System (JSON)

Target: telecom_dw.db (SQLite) containing 1 fact table + 5 dimension tables
"""

import pandas as pd
import json
import sqlite3
from datetime import datetime
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
SOURCE_DIR = PROJECT_DIR / "data-sources"
OUTPUT_DIR = PROJECT_DIR / "outputs"
OUTPUT_DIR.mkdir(exist_ok=True)
DB_PATH = PROJECT_DIR / "telecom_dw_new.db"

log = []
def step(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}")
    log.append(f"[{ts}] {msg}")


# =====================================================================
# EXTRACT
# =====================================================================
step("EXTRACT: Reading Source 1 - Customer & Billing System (CSV)")
source1 = pd.read_csv(f"{SOURCE_DIR}/customer_billing_source.csv")
step(f"  -> {source1.shape[0]} rows, {source1.shape[1]} columns extracted")

step("EXTRACT: Reading Source 2 - Location Reference System (CSV)")
source2 = pd.read_csv(f"{SOURCE_DIR}/location_reference_source.csv")
step(f"  -> {source2.shape[0]} rows, {source2.shape[1]} columns extracted")

step("EXTRACT: Reading Source 3 - Service Provisioning System (JSON)")
with open(f"{SOURCE_DIR}/service_subscription_source.json") as f:
    source3 = pd.DataFrame(json.load(f))
step(f"  -> {source3.shape[0]} rows, {source3.shape[1]} columns extracted")


# =====================================================================
# TRANSFORM
# =====================================================================
step("TRANSFORM: Joining sources on Zip_Code and CustomerID")
merged = source1.merge(source2, on="Zip_Code", how="left")
merged = merged.merge(source3, on="CustomerID", how="left")
step(f"  -> Merged dataset: {merged.shape[0]} rows, {merged.shape[1]} columns")

step("TRANSFORM: Missing value handling")
blank_before = (merged['Total_Charges'].astype(str).str.strip() == '').sum()
merged['Total_Charges'] = merged['Total_Charges'].astype(str).str.strip().replace('', '0').astype(float)
step(f"  -> Fixed {blank_before} blank Total_Charges values (set to 0, cast to float)")

null_reason_before = merged['Churn_Reason'].isnull().sum()
merged['Churn_Reason'] = merged['Churn_Reason'].fillna('Not Applicable - Active Customer')
step(f"  -> Filled {null_reason_before} null Churn_Reason values")

step("TRANSFORM: Deduplication check")
dupes = merged['CustomerID'].duplicated().sum()
step(f"  -> Duplicate CustomerIDs found: {dupes} (none removed - already clean)")

step("TRANSFORM: Standardisation - trimming whitespace on text columns")
text_cols = merged.select_dtypes(include='object').columns
for c in text_cols:
    merged[c] = merged[c].astype(str).str.strip()
step(f"  -> Trimmed {len(text_cols)} text columns")

step("TRANSFORM: Derived attribute creation")
merged['Avg_Monthly_Spend'] = (merged['Total_Charges'] / merged['Tenure_Months'].replace(0, 1)).round(2)
merged['Tenure_Years'] = (merged['Tenure_Months'] / 12).round(1)
step("  -> Created Avg_Monthly_Spend and Tenure_Years derived columns")

step("TRANSFORM: Data type conversion check")
merged['Churn_Value'] = merged['Churn_Value'].astype(int)
merged['Churn_Score'] = merged['Churn_Score'].astype(int)
merged['CLTV'] = merged['CLTV'].astype(int)
step("  -> Confirmed Churn_Value, Churn_Score, CLTV as integer types")

merged.to_pickle(OUTPUT_DIR / "cleaned_master.pkl")
step(f"TRANSFORM COMPLETE: Final cleaned dataset {merged.shape[0]} rows x {merged.shape[1]} columns")


# =====================================================================
# LOAD
# =====================================================================
step("LOAD: Connecting to target warehouse (SQLite)")
conn = sqlite3.connect(DB_PATH)

step("LOAD: Building Dim_Customer")
dim_customer = merged[['CustomerID','Gender','Senior_Citizen','Partner','Dependents']].drop_duplicates().reset_index(drop=True)
dim_customer.insert(0, 'CustomerKey', range(1, len(dim_customer)+1))
dim_customer.to_sql('Dim_Customer', conn, if_exists='replace', index=False)
step(f"  -> Loaded {len(dim_customer)} rows")

step("LOAD: Building Dim_Location")
dim_location = merged[['City','Zip_Code','Latitude','Longitude']].drop_duplicates(subset='Zip_Code').reset_index(drop=True)
dim_location.insert(0, 'LocationKey', range(1, len(dim_location)+1))
dim_location.to_sql('Dim_Location', conn, if_exists='replace', index=False)
step(f"  -> Loaded {len(dim_location)} rows")

step("LOAD: Building Dim_Service (junk dimension)")
service_cols = ['Phone_Service','Multiple_Lines','Internet_Service','Online_Security',
                'Online_Backup','Device_Protection','Tech_Support','Streaming_TV','Streaming_Movies']
dim_service = merged[service_cols].drop_duplicates().reset_index(drop=True)
dim_service.insert(0, 'ServiceKey', range(1, len(dim_service)+1))
dim_service.to_sql('Dim_Service', conn, if_exists='replace', index=False)
step(f"  -> Loaded {len(dim_service)} rows")

step("LOAD: Building Dim_Contract")
dim_contract = merged[['Contract','Paperless_Billing','Payment_Method']].drop_duplicates().reset_index(drop=True)
dim_contract.insert(0, 'ContractKey', range(1, len(dim_contract)+1))
dim_contract.to_sql('Dim_Contract', conn, if_exists='replace', index=False)
step(f"  -> Loaded {len(dim_contract)} rows")

step("LOAD: Building Dim_Date")
dim_date = pd.DataFrame([{'DateKey': 1, 'FullDate': '2023-09-30', 'Year': 2023, 'Quarter': 'Q3', 'MonthName': 'September'}])
dim_date.to_sql('Dim_Date', conn, if_exists='replace', index=False)
step(f"  -> Loaded {len(dim_date)} row")

step("LOAD: Building Fact_CustomerAccount (attaching surrogate keys)")
fact = merged.merge(dim_customer, on=['CustomerID','Gender','Senior_Citizen','Partner','Dependents'], how='left')
fact = fact.merge(dim_location, on=['City','Zip_Code','Latitude','Longitude'], how='left')
fact = fact.merge(dim_service, on=service_cols, how='left')
fact = fact.merge(dim_contract, on=['Contract','Paperless_Billing','Payment_Method'], how='left')
fact['DateKey'] = 1

fact_table = fact[['CustomerKey','LocationKey','ServiceKey','ContractKey','DateKey',
                    'Tenure_Months','Monthly_Charges','Total_Charges','Avg_Monthly_Spend',
                    'Tenure_Years','Churn_Value','Churn_Score','CLTV']].copy()
fact_table.insert(0, 'CustomerAccountKey', range(1, len(fact_table)+1))
fact_table.to_sql('Fact_CustomerAccount', conn, if_exists='replace', index=False)
step(f"  -> Loaded {len(fact_table)} rows into Fact_CustomerAccount")


# =====================================================================
# VALIDATION
# =====================================================================
step("VALIDATION: Checking for null foreign keys")
null_fks = fact_table[['CustomerKey','LocationKey','ServiceKey','ContractKey','DateKey']].isnull().sum().sum()
step(f"  -> Null foreign keys found: {null_fks} (expected: 0)")

step("VALIDATION: Comparing churn rate before vs after load")
rate_before = merged['Churn_Value'].mean()
rate_after = fact_table['Churn_Value'].mean()
step(f"  -> Source churn rate: {rate_before:.4%} | Warehouse churn rate: {rate_after:.4%} | Match: {rate_before == rate_after}")

step("VALIDATION: Row count reconciliation")
step(f"  -> Source rows: {source1.shape[0]} | Fact table rows: {fact_table.shape[0]} | Match: {source1.shape[0] == fact_table.shape[0]}")

conn.close()
step("ETL PIPELINE COMPLETE")

with open(OUTPUT_DIR / "etl_log.txt", "w") as f:
    f.write("\n".join(log))
