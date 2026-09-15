# Historical QC-only batch v1
POST /api/v1/admin/qc-batches {"date":"YYYY-MM-DD"} (Beijing date, admin mutation).
GET /api/v1/admin/qc-batches?date=YYYY-MM-DD returns latest durable batch or null.
Response: {request_id,status,items:[{kind: qc|display,id,time,radar,status,error}]}.
Snapshots include all normalized scans of the day and exact contributing scans of existing analyses.
Each scan is computed once per batch; missing normalization is not a successful QC result.
QC and display jobs are bounded, durable and independently tracked. Display uses existing
analysis geometry and latest QC. Grid, mosaic, QPE and forecasts are not regenerated.
