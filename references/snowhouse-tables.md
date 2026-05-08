# Snowhouse Metering Tables Reference

## Deployment → Database Mapping

| Deployment | Metering Database | Account View |
|---|---|---|
| va2 | METERING_BY_HOUR_VA2.METERING | SNOWHOUSE_VIEWS.VA2.ACCOUNT_ETL_V |
| va3 | METERING_BY_HOUR_VA3.METERING | SNOWHOUSE_VIEWS.VA3.ACCOUNT_ETL_V |
| prod3 | METERING_BY_HOUR_PROD3.METERING | SNOWHOUSE_VIEWS.PROD3.ACCOUNT_ETL_V |

Pattern: `METERING_BY_HOUR_<DEPLOYMENT>.METERING.<TABLE>`

## Account Lookup

```sql
SELECT id, account_id, name, company_name, created_on, state
FROM SNOWHOUSE_VIEWS.<deployment>.ACCOUNT_ETL_V
WHERE name = '<LOCATOR>'
```

**CRITICAL**: The `id` field is what you use to filter metering tables (NOT `account_id` which is always 0).

## Metering Tables

### WAREHOUSE_METERING
- Filter: `WHERE account_id = <id>`
- Columns: entity_id, usage_time (TIMESTAMP_TZ), credits, qas_credits
- entity_id = warehouse entity ID (map to names via user input)

### COMPUTE_SERVICE_METERING
- Filter: `WHERE account_id = <id>`
- Columns: event_type, entity_id, usage_time, credits
- Key event_types:
  - `COMPUTE_SERVICE_CLUSTERING` — automatic clustering (entity_id = table ID)
  - `COMPUTE_SERVICE_USER_SCHEDULED_TASK` — serverless tasks
  - `COMPUTE_SERVICE_TRUST_CENTER_TASKS` — trust center
  - `COMPUTE_SERVICE_SNOWFLAKEDB_UPGRADE` — internal upgrades
  - `COMPUTE_SERVICE_DEFRAGMENTATION` — table defrag

### GS_METERING
- Filter: `WHERE account_id = <id>`
- Columns: usage_time, credits
- Cloud services (metadata ops, query compilation)

### AI_SERVICES_METERING
- Filter: `WHERE account_id = <id>`
- Columns: event_type, sku, usage_time, credits
- event_types map to AI products (Cortex Analyst, Search, Functions, Agents, SI, etc.)

### STORAGE_SIMPLE_AVG_METERING
- Filter: `WHERE account_id = <id>`
- Columns: usage_date (DATE, not TIMESTAMP), bytes, event_type
- **WARNING**: Has entity_id=0 rows (account-level aggregate). These DUPLICATE the sum of individual entity rows. Always filter `WHERE entity_id != 0` or use only entity_id=0 row, never both.
- Convert: `SUM(bytes) / POW(1024, 4)` = TB

### SNOW_SERVICES_METERING
- Filter: `WHERE account_id = <id>`
- Columns: usage_time, credits, event_type
- Snowpark Container Services compute

### DATA_TRANSFER_METERING
- Filter: `WHERE account_id = <id>`
- Columns: usage_time, bytes (NOT credits!)
- Convert: `SUM(bytes) / POW(1024, 3)` = GB

## Common Gotchas

1. **Timestamps**: All metering timestamps are TIMESTAMP_TZ (UTC). After loading into pandas, call `.dt.tz_localize(None)` to strip timezone for plotting.

2. **Storage double-count**: STORAGE_SIMPLE_AVG_METERING includes entity_id=0 which is the SUM of all other entities. Use one or the other, never both.

3. **DATA_TRANSFER has no credits column**: Only bytes. You cannot directly get credit cost of data transfer from this table.

4. **Warehouse entity IDs**: The entity_id in WAREHOUSE_METERING maps to the warehouse object ID in the customer's account. These cannot be resolved to names from Snowhouse alone — you need the customer to provide the mapping (or display as entity IDs).

5. **Clustering entity IDs**: In COMPUTE_SERVICE_METERING WHERE event_type = 'COMPUTE_SERVICE_CLUSTERING', the entity_id is the TABLE object ID (not warehouse). These also cannot be resolved to table names from Snowhouse.

6. **Connection**: Always use `SNOWHOUSE_AWS_US_WEST_2` connection name.

7. **Decimal types**: Snowflake returns Decimal objects. Convert with: `float(x) if isinstance(x, decimal.Decimal) else x`
