"""Check reorder dates against the public input snapshot's business policy."""
from pathlib import Path
import tempfile


def date_status(records, expected, parse_date, to_int):
    """A non-reorder item has no date; Excel may represent that as blank or N/A."""
    actual = {}
    errors = []
    for row in records:
        item_id = to_int(row["Product_ID"])
        if item_id is None or item_id in actual:
            errors.append({"product_id": row["Product_ID"], "reason": "invalid_or_duplicate_id"})
        else:
            actual[item_id] = row
    if set(actual) != set(expected):
        errors.append({"reason": "product_set_mismatch", "missing": sorted(set(expected) - set(actual)),
                       "unexpected": sorted(set(actual) - set(expected))})
    for item_id in sorted(set(actual) & set(expected)):
        value = actual[item_id]["Reorder_Date"]
        target = expected[item_id]
        if target["needs_reorder"]:
            if parse_date(value) != target["reorder_date"]:
                errors.append({"product_id": item_id, "reason": "wrong_reorder_date",
                               "actual": str(value), "expected": target["reorder_date"].isoformat()})
        elif not (value is None or isinstance(value, str) and value.strip().lower() in {"", "n/a"}):
            errors.append({"product_id": item_id, "reason": "unexpected_reorder_date", "actual": str(value)})
    return not errors, errors


def verify_dates(module, inputs, outputs):
    """Use the same source-derived expectations and LibreOffice as the upstream oracle."""
    from openpyxl import load_workbook

    expected, _, _ = module.derive_expected(inputs)
    with tempfile.TemporaryDirectory(prefix="journey-inventory-") as directory:
        calculated, detail = module.roundtrip_with_libreoffice(outputs / module.OUTPUT_NAME, Path(directory))
        if calculated is None:
            return False, [{"reason": "workbook_recalculation_failed", "detail": detail}]
        workbook = load_workbook(calculated, data_only=True)
        try:
            records, missing = module.sheet_records(workbook, "Stock Analysis", module.STOCK_HEADERS)
        finally:
            workbook.close()
    if missing:
        return False, [{"reason": "missing_columns", "columns": missing}]
    return date_status(records, expected, module.parse_date, module.to_int)
