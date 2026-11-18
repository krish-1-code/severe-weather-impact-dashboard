"""Inspect, clean and explore the supplied NOAA 2024 Storm Events details CSV.

Run: python analyze_weather.py
Only pandas and matplotlib are required. The input is never modified. All
derived files go to analysis_outputs beside this script (or --output-dir).
See README.md for definitions, source links and interpretation limits.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
from datetime import datetime, timezone
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # Save figures; no desktop window is needed.
import matplotlib.pyplot as plt
import pandas as pd


EXPECTED_COLUMNS = """BEGIN_YEARMONTH BEGIN_DAY BEGIN_TIME END_YEARMONTH END_DAY
END_TIME EPISODE_ID EVENT_ID STATE STATE_FIPS YEAR MONTH_NAME EVENT_TYPE CZ_TYPE
CZ_FIPS CZ_NAME WFO BEGIN_DATE_TIME CZ_TIMEZONE END_DATE_TIME INJURIES_DIRECT
INJURIES_INDIRECT DEATHS_DIRECT DEATHS_INDIRECT DAMAGE_PROPERTY DAMAGE_CROPS
SOURCE MAGNITUDE MAGNITUDE_TYPE FLOOD_CAUSE CATEGORY TOR_F_SCALE TOR_LENGTH
TOR_WIDTH TOR_OTHER_WFO TOR_OTHER_CZ_STATE TOR_OTHER_CZ_FIPS TOR_OTHER_CZ_NAME
BEGIN_RANGE BEGIN_AZIMUTH BEGIN_LOCATION END_RANGE END_AZIMUTH END_LOCATION
BEGIN_LAT BEGIN_LON END_LAT END_LON EPISODE_NARRATIVE EVENT_NARRATIVE DATA_SOURCE""".split()
COUNTS = ["INJURIES_DIRECT", "INJURIES_INDIRECT", "DEATHS_DIRECT", "DEATHS_INDIRECT"]
NUMERIC = """BEGIN_YEARMONTH BEGIN_DAY BEGIN_TIME END_YEARMONTH END_DAY END_TIME
YEAR INJURIES_DIRECT INJURIES_INDIRECT DEATHS_DIRECT DEATHS_INDIRECT MAGNITUDE
CATEGORY TOR_LENGTH TOR_WIDTH BEGIN_RANGE END_RANGE BEGIN_LAT BEGIN_LON END_LAT
END_LON""".split()
INTEGER = set("""BEGIN_YEARMONTH BEGIN_DAY BEGIN_TIME END_YEARMONTH END_DAY END_TIME
YEAR INJURIES_DIRECT INJURIES_INDIRECT DEATHS_DIRECT DEATHS_INDIRECT CATEGORY
TOR_WIDTH""".split())
WATER_REGIONS = {"ATLANTIC NORTH", "ATLANTIC SOUTH", "E PACIFIC", "GUAM WATERS",
                 "GULF OF ALASKA", "GULF OF MEXICO", "LAKE ERIE", "LAKE HURON",
                 "LAKE MICHIGAN", "LAKE ONTARIO", "LAKE ST CLAIR", "LAKE SUPERIOR",
                 "ST LAWRENCE R"}
MONTHS = {name: number for number, name in enumerate(
    "JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC".split(), 1)}
SOURCES = {
    "noaa_database": "https://www.ncei.noaa.gov/access/storm-events-database/",
    "bulk_format": "https://www.ncei.noaa.gov/pub/data/swdi/stormevents/csvfiles/Storm-Data-Bulk-csv-Format.pdf",
    "preparation_instructions": "https://www.weather.gov/media/directives/010_pdfs/pd01016005curr.pdf",
    "noaa_faq": "https://www.ncdc.noaa.gov/stormevents/faq.jsp",
    "supplied_download": "https://raw.githubusercontent.com/oedp/noaa_storm_events/main/StormEvents_details-ftp_v1.0_d2024_c20250818.csv.gz",
}


def file_hash(path: Path) -> str:
    """Fingerprint the original file without loading another copy into memory."""
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_damage(values: pd.Series) -> tuple[pd.Series, pd.Series]:
    """Convert K/M/B to USD; never turn blank/unsupported text into zero."""
    text = values.str.strip().str.upper()
    parts = text.str.extract(r"^(\d+(?:\.\d+)?)([KMB]?)$")
    multiplier = parts[1].map({"": 1, "K": 1_000, "M": 1_000_000, "B": 1_000_000_000})
    # Round at USD-cent precision so 4.18M exports as 4180000.0 rather than
    # 4179999.9999999995. Raw source text remains available for inspection.
    dollars = (pd.to_numeric(parts[0], errors="coerce") * multiplier).round(2).astype("Float64")
    status = pd.Series("invalid", index=values.index, dtype="string")
    status.loc[text.isna()] = "missing"
    status.loc[dollars.eq(0).fillna(False)] = "reported_zero"
    status.loc[dollars.gt(0).fillna(False)] = "reported_positive"
    return dollars, status


def parse_text_date(values: pd.Series) -> pd.Series:
    """Parse the observed DD-MON-YY format without relying on OS month names."""
    parts = values.str.upper().str.extract(r"^(\d{2})-([A-Z]{3})-(\d{2}) (\d{2}:\d{2}:\d{2})$")
    iso = "20" + parts[2] + "-" + parts[1].map(MONTHS).astype("Int64").astype("string").str.zfill(2)
    iso = iso + "-" + parts[0] + " " + parts[3]
    return pd.to_datetime(iso, format="%Y-%m-%d %H:%M:%S", errors="coerce")


def component_date(frame: pd.DataFrame, prefix: str) -> pd.Series:
    """Independently reconstruct timestamps to check the redundant date fields."""
    yearmonth = frame[f"{prefix}_YEARMONTH"]
    hhmm = frame[f"{prefix}_TIME"]
    result = pd.to_datetime({"year": yearmonth // 100, "month": yearmonth % 100,
                            "day": frame[f"{prefix}_DAY"], "hour": hhmm // 100,
                            "minute": hhmm % 100}, errors="coerce")
    return result.mask(~(hhmm.between(0, 2359) & (hhmm % 100).between(0, 59)))


def summarize(frame: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
    """Sum available components with coverage counts; all-missing sums stay NA."""
    grouped = frame.groupby(keys, dropna=False, observed=True)
    result = grouped.size().rename("event_records").to_frame()
    result["distinct_episodes"] = grouped["EPISODE_ID"].nunique()
    for field in COUNTS + ["INJURIES_TOTAL", "DEATHS_TOTAL"]:
        result[field.lower()] = grouped[field].sum(min_count=1)
    for kind in ["PROPERTY", "CROPS"]:
        dollars = f"DAMAGE_{kind}_USD"
        label = kind.lower()
        result[f"{label}_reported_usd_sum"] = grouped[dollars].sum(min_count=1)
        known = grouped[dollars].count()
        result[f"{label}_known_records"] = known
        result[f"{label}_unknown_records"] = result.event_records - known
        result[f"{label}_coverage_pct"] = 100 * known / result.event_records
    result["both_damage_components_known_records"] = grouped["DAMAGE_TOTAL_COMPLETE_USD"].count()
    result["complete_case_damage_usd_sum"] = grouped["DAMAGE_TOTAL_COMPLETE_USD"].sum(min_count=1)
    # This adds available components, including one-sided records. It is NOT a
    # complete total loss estimate. An entirely unreported group remains NA.
    result["available_damage_components_usd_sum"] = result[
        ["property_reported_usd_sum", "crops_reported_usd_sum"]].sum(axis=1, min_count=1)
    return result.reset_index()


def save_csv(frame: pd.DataFrame, directory: Path, name: str) -> None:
    frame.to_csv(directory / name, index=False, na_rep="", float_format="%.6f")


def make_charts(by_type: pd.DataFrame, monthly: pd.DataFrame, regions: pd.DataFrame, out: Path) -> None:
    plt.rcParams.update({"font.size": 10, "axes.spines.top": False, "axes.spines.right": False})
    fig, axes = plt.subplots(1, 2, figsize=(14, 6), layout="constrained")
    for ax, measure, title, divisor in [
        (axes[0], "event_records", "Most frequently reported event types", 1),
        (axes[1], "available_damage_components_usd_sum", "Largest sums of reported damage components", 1e9),
    ]:
        top = by_type.nlargest(10, measure).sort_values(measure)
        ax.barh(top.EVENT_TYPE, top[measure] / divisor, color="#23788a")
        ax.set_title(title)
        ax.set_xlabel("Event records" if divisor == 1 else "Reported USD billions; missing estimates excluded")
    fig.suptitle("2024 NOAA Storm Events — supplied August 2025 file vintage")
    fig.savefig(out / "event_types.png", dpi=160)
    plt.close(fig)
    fig, ax = plt.subplots(figsize=(10, 5), layout="constrained")
    ax.bar(monthly.BEGIN_MONTH.astype(str), monthly.event_records, color="#23788a")
    ax.tick_params(axis="x", rotation=45)
    ax.set(title="Reported event records by local start month (2024)",
           ylabel="Event records", xlabel="Local start month; records are not unique storms")
    fig.savefig(out / "monthly_records.png", dpi=160)
    plt.close(fig)
    fig, axes = plt.subplots(2, 2, figsize=(14, 9), layout="constrained")
    x = range(len(monthly))
    labels = monthly.BEGIN_MONTH.astype(str).str[-2:]
    axes[0, 0].bar(x, monthly.property_reported_usd_sum / 1e9, label="Property", color="#23788a")
    axes[0, 0].bar(x, monthly.crops_reported_usd_sum / 1e9,
                   bottom=monthly.property_reported_usd_sum / 1e9, label="Crops", color="#d38c3c")
    axes[0, 0].set(title="Available reported damage components", ylabel="USD billions; missing estimates excluded")
    axes[0, 0].legend()
    axes[0, 1].bar(x, monthly.event_records, color="#23788a")
    axes[0, 1].set(title="Reporting frequency", ylabel="Event records")
    axes[1, 0].plot(x, monthly.deaths_total, marker="o", label="Deaths", color="#b6554a")
    axes[1, 0].plot(x, monthly.injuries_total, marker="o", label="Injuries", color="#23788a")
    axes[1, 0].set(title="Reported human impacts", ylabel="Direct + indirect counts")
    axes[1, 0].legend()
    axes[1, 1].plot(x, monthly.property_coverage_pct, marker="o", label="Property", color="#23788a")
    axes[1, 1].plot(x, monthly.crops_coverage_pct, marker="o", label="Crops", color="#d38c3c")
    axes[1, 1].set(title="Damage estimate availability", ylabel="Records with known estimate (%)", ylim=(0, 100))
    axes[1, 1].legend()
    for ax in axes.flat:
        ax.set_xticks(list(x), labels)
        ax.set_xlabel("Recorded local start month (2024)")
    fig.suptitle("2024 monthly reported impacts — one file vintage, not a seasonal forecast", fontsize=15)
    fig.savefig(out / "monthly_impacts.png", dpi=160)
    plt.close(fig)
    fig, axes = plt.subplots(1, 3, figsize=(18, 7), layout="constrained")
    count_top = regions.nlargest(10, "event_records").sort_values("event_records")
    damage_top = regions.nlargest(10, "available_damage_components_usd_sum").sort_values("available_damage_components_usd_sum")
    axes[0].barh(count_top.STATE, count_top.event_records, color="#23788a")
    axes[0].set(title="Most reported records", xlabel="Event records")
    axes[1].barh(damage_top.STATE, damage_top.available_damage_components_usd_sum / 1e9, color="#d38c3c")
    axes[1].set(title="Largest available damage sums", xlabel="Reported USD billions")
    axes[2].barh(count_top.STATE, count_top.property_coverage_pct, color="#23788a")
    axes[2].set(title="Property estimate coverage\nfor the ten most reported regions", xlabel="Known estimates (% of records)", xlim=(0, 100))
    fig.suptitle("State/region comparisons — reporting and exposure differ; these are not risk rankings", fontsize=15)
    fig.savefig(out / "location_comparisons.png", dpi=160)
    plt.close(fig)


def export_power_bi_events(clean: pd.DataFrame, out: Path) -> None:
    """One row per event, without narratives, for a small local Power BI model."""
    columns = {
        "EVENT_ID": "Event ID", "EPISODE_ID": "Episode ID", "EVENT_TYPE": "Event Type",
        "STATE": "State Region", "CZ_TYPE": "Location Type", "CZ_NAME": "Area Name",
        "NOAA_AREA_KEY": "NOAA Area Key", "BEGIN_MONTH": "Start Month",
        "DAMAGE_PROPERTY_USD": "Property USD", "DAMAGE_CROPS_USD": "Crop USD",
        "DAMAGE_TOTAL_COMPLETE_USD": "Complete Damage USD",
        "DAMAGE_PROPERTY_STATUS": "Property Status", "DAMAGE_CROPS_STATUS": "Crop Status",
        "DEATHS_DIRECT": "Direct Deaths", "DEATHS_INDIRECT": "Indirect Deaths",
        "INJURIES_DIRECT": "Direct Injuries", "INJURIES_INDIRECT": "Indirect Injuries",
        "DEATHS_TOTAL": "Deaths", "INJURIES_TOTAL": "Injuries",
    }
    fact = clean[list(columns)].rename(columns=columns).copy()
    fact["Marine Context"] = clean.MARINE_CONTEXT.map({True: "Marine context", False: "Nonmarine context"})
    fact["Has Begin Coordinates"] = clean.HAS_BEGIN_COORDINATES.astype("int64")
    fact["Local Start Date"] = clean.BEGIN_LOCAL_DATETIME.dt.strftime("%Y-%m-%d")
    save_csv(fact, out, "events_for_power_bi.csv")


def run(input_path: Path, out: Path) -> None:
    if not input_path.is_file():
        raise FileNotFoundError(f"CSV not found: {input_path}")
    if input_path == out or out in input_path.parents:
        raise ValueError("Choose an output directory separate from the source CSV directory.")
    before_hash = file_hash(input_path)
    # Load as text first: preserve IDs, codes and original damage notation. Empty
    # cells alone are missing; literal strings such as 'NA' are not guessed away.
    raw = pd.read_csv(input_path, dtype="string", keep_default_na=False)
    missing_columns = sorted(set(EXPECTED_COLUMNS) - set(raw.columns))
    if missing_columns:
        raise ValueError(f"Required columns absent: {missing_columns}")
    if raw.empty:
        raise ValueError("The input contains no records.")
    clean = raw.apply(lambda column: column.str.strip()).replace("", pd.NA)
    # Narratives are source text. Preserve their spacing verbatim.
    for column in ["EPISODE_NARRATIVE", "EVENT_NARRATIVE"]:
        clean[column] = raw[column].replace("", pd.NA)
    exact_duplicates = int(raw.duplicated().sum())
    duplicate_ids = int(clean.EVENT_ID.duplicated(keep=False).sum())
    if clean.EVENT_ID.isna().any() or duplicate_ids:
        raise ValueError("Missing/duplicate EVENT_ID: review source records before aggregation; no rows removed.")
    out.mkdir(parents=True, exist_ok=True)
    issues: list[pd.DataFrame] = []

    def flag(mask: pd.Series, field: str, issue: str) -> None:
        rows = clean.loc[mask.fillna(False), ["EVENT_ID", "EPISODE_ID"]].copy()
        rows["source_csv_record_number"] = rows.index + 1  # Excludes header; not a physical narrative line.
        rows["field"] = field
        rows["issue"] = issue
        rows["raw_value"] = raw.loc[rows.index, field] if field in raw else ""
        issues.append(rows)

    numeric_profiles = []
    for column in NUMERIC:
        values = pd.to_numeric(clean[column], errors="coerce").astype("Float64")
        invalid = clean[column].notna() & values.isna()
        flag(invalid, column, "invalid_numeric")
        fraction = values.mod(1).ne(0) if column in INTEGER else pd.Series(False, index=clean.index)
        flag(fraction, column, "unexpected_fractional_integer")
        negative = values.lt(0) if column not in ["BEGIN_LAT", "BEGIN_LON", "END_LAT", "END_LON"] else pd.Series(False, index=clean.index)
        flag(negative, column, "unexpected_negative")
        numeric_profiles.append({"column": column, "known_records": int(values.count()),
                                 "invalid_numeric": int(invalid.sum()), "negative_flagged": int(negative.sum()),
                                 "fractional_flagged": int(fraction.sum()),
                                 "minimum": values.min(), "median": values.median(), "maximum": values.max()})
        clean[column] = values.astype("Int64") if column in INTEGER and not fraction.any() else values
    for column in ["EVENT_ID", "EPISODE_ID", "STATE_FIPS", "CZ_FIPS", "TOR_OTHER_CZ_FIPS"]:
        flag(clean[column].notna() & ~clean[column].str.fullmatch(r"\d+"), column, "invalid_identifier_or_code")
    for prefix in ["BEGIN", "END"]:
        dates = parse_text_date(clean[f"{prefix}_DATE_TIME"])
        clean[f"{prefix}_LOCAL_DATETIME"] = dates
        flag(dates.isna(), f"{prefix}_DATE_TIME", "unparseable_date")
        reconstructed = component_date(clean, prefix)
        flag(reconstructed.isna() | dates.ne(reconstructed), f"{prefix}_DATE_TIME", "date_component_mismatch")
    clean["BEGIN_MONTH"] = clean.BEGIN_LOCAL_DATETIME.dt.strftime("%Y-%m")
    clean["DURATION_HOURS"] = (clean.END_LOCAL_DATETIME - clean.BEGIN_LOCAL_DATETIME).dt.total_seconds() / 3600
    flag(clean.DURATION_HOURS.lt(0), "END_DATE_TIME", "end_before_begin")
    flag(clean.YEAR.ne(clean.BEGIN_LOCAL_DATETIME.dt.year), "YEAR", "begin_year_mismatch")
    flag(clean.BEGIN_LOCAL_DATETIME.dt.year.ne(2024), "BEGIN_DATE_TIME", "outside_expected_2024")
    month_names = clean.BEGIN_LOCAL_DATETIME.dt.month.map(dict(enumerate(
        "January February March April May June July August September October November December".split(), 1)))
    flag(clean.MONTH_NAME.ne(month_names), "MONTH_NAME", "month_name_mismatch")
    flag(~clean.CZ_TIMEZONE.str.fullmatch(r"[A-Z]+[+-]?\d{1,2}"), "CZ_TIMEZONE", "unexpected_timezone_label")
    flag(clean.CZ_TIMEZONE.isin(["PDT-7", "EDT-4", "CDT-5"]), "CZ_TIMEZONE", "daylight_timezone_label_for_review")
    # No automatic UTC/DST inference: use the supplied local start month.
    for column, limit in [("BEGIN_LAT", 90), ("END_LAT", 90), ("BEGIN_LON", 180), ("END_LON", 180)]:
        flag(clean[column].abs().gt(limit), column, "coordinate_out_of_range")
    for prefix in ["BEGIN", "END"]:
        flag(clean[f"{prefix}_LAT"].isna() ^ clean[f"{prefix}_LON"].isna(), f"{prefix}_LAT", "incomplete_coordinate_pair")
    clean["HAS_BEGIN_COORDINATES"] = clean.BEGIN_LAT.notna() & clean.BEGIN_LON.notna()
    for column in ["DAMAGE_PROPERTY", "DAMAGE_CROPS"]:
        clean[f"{column}_USD"], clean[f"{column}_STATUS"] = parse_damage(clean[column])
        flag(clean[f"{column}_STATUS"].eq("invalid"), column, "unsupported_damage_value")
    # Requiring both inputs prevents partial totals from looking complete.
    clean["DAMAGE_TOTAL_COMPLETE_USD"] = clean.DAMAGE_PROPERTY_USD + clean.DAMAGE_CROPS_USD
    clean["DEATHS_TOTAL"] = clean.DEATHS_DIRECT + clean.DEATHS_INDIRECT
    clean["INJURIES_TOTAL"] = clean.INJURIES_DIRECT + clean.INJURIES_INDIRECT
    clean["MARINE_CONTEXT"] = (clean.STATE.isin(WATER_REGIONS) |
                               clean.EVENT_TYPE.str.startswith("Marine ") | clean.EVENT_TYPE.eq("Waterspout"))
    flag(clean.MARINE_CONTEXT & clean.CZ_TYPE.ne("M"), "CZ_TYPE", "marine_context_without_M_designator")
    flag(~clean.CZ_TYPE.isin(["C", "Z", "M"]), "CZ_TYPE", "unexpected_location_designator")
    clean["NOAA_AREA_KEY"] = clean.STATE + "|" + clean.CZ_TYPE + "|" + clean.CZ_FIPS.str.zfill(3)
    # This is a NOAA reporting-area key, NOT a Census county GEOID.

    profile = pd.DataFrame({"column": raw.columns,
                            "raw_dtype": [str(raw[c].dtype) for c in raw],
                            "clean_dtype": [str(clean[c].dtype) for c in raw],
                            "missing_records": [int(clean[c].isna().sum()) for c in raw],
                            "distinct_nonmissing": [int(clean[c].nunique()) for c in raw],
                            "trimmed_records": [int(raw[c].ne(raw[c].str.strip()).sum()) if c not in ["EPISODE_NARRATIVE", "EVENT_NARRATIVE"] else 0 for c in raw]})
    profile["missing_pct"] = profile.missing_records / len(raw) * 100
    save_csv(profile, out, "column_profile.csv")
    save_csv(pd.DataFrame(numeric_profiles), out, "numeric_profile.csv")
    quality_issues = pd.concat(issues, ignore_index=True)
    save_csv(quality_issues, out, "quality_issues.csv")
    damage_audit = []
    for column in ["DAMAGE_PROPERTY", "DAMAGE_CROPS"]:
        audit = clean.groupby([column, f"{column}_USD", f"{column}_STATUS"], dropna=False).size().rename("event_records").reset_index()
        audit.columns = ["damage_text", "parsed_usd", "status", "event_records"]
        audit.insert(0, "field", column)
        damage_audit.append(audit)
    save_csv(pd.concat(damage_audit, ignore_index=True), out, "damage_value_audit.csv")
    tables = {}
    for name, keys in {"by_event_type": ["EVENT_TYPE"], "by_state_region": ["STATE"],
                       "by_month": ["BEGIN_MONTH"], "by_location_type": ["CZ_TYPE", "MARINE_CONTEXT"],
                       "by_reporting_area": ["NOAA_AREA_KEY", "STATE", "CZ_TYPE", "CZ_FIPS", "CZ_NAME"]}.items():
        table = summarize(clean, keys)
        table = table.sort_values(keys if name == "by_month" else ["event_records"], ascending=name == "by_month")
        save_csv(table, out, f"{name}.csv")
        tables[name] = table
    coordinate_coverage = clean.groupby(["CZ_TYPE", "MARINE_CONTEXT"], dropna=False).agg(
        event_records=("EVENT_ID", "size"), begin_coordinate_records=("HAS_BEGIN_COORDINATES", "sum")).reset_index()
    coordinate_coverage["begin_coordinate_coverage_pct"] = 100 * coordinate_coverage.begin_coordinate_records / coordinate_coverage.event_records
    save_csv(coordinate_coverage, out, "coordinate_coverage.csv")
    # Event-type-specific profiles avoid mixing hail inches with wind knots.
    magnitudes = clean.groupby(["EVENT_TYPE", "MAGNITUDE_TYPE"], dropna=False).MAGNITUDE.agg(
        known_records="count", minimum="min", median="median", maximum="max").reset_index()
    save_csv(magnitudes, out, "magnitude_by_event_type.csv")
    rank_fields = ["EVENT_ID", "EPISODE_ID", "STATE", "CZ_TYPE", "CZ_NAME", "EVENT_TYPE", "BEGIN_DATE_TIME",
                   "DAMAGE_PROPERTY_USD", "DAMAGE_CROPS_USD", "DAMAGE_TOTAL_COMPLETE_USD"]
    save_csv(clean.nlargest(20, "DAMAGE_PROPERTY_USD")[rank_fields], out, "largest_property_damage_records.csv")
    clean.to_csv(out / "cleaned_events_2024.csv.gz", index=False, na_rep="",
                 date_format="%Y-%m-%d %H:%M:%S", compression={"method": "gzip", "mtime": 0})
    make_charts(tables["by_event_type"], tables["by_month"], tables["by_state_region"], out)
    export_power_bi_events(clean, out)

    # Reconciliation checks catch lost records, missing-value conversion errors
    # and accidental input changes during this run.
    for table in tables.values():
        assert int(table.event_records.sum()) == len(raw), "Grouped record count does not reconcile"
        for field in COUNTS + ["DEATHS_TOTAL", "INJURIES_TOTAL"]:
            assert table[field.lower()].sum(min_count=1) == clean[field].sum(min_count=1)
        for kind in ["PROPERTY", "CROPS"]:
            assert abs(float(table[f"{kind.lower()}_reported_usd_sum"].sum(min_count=1)) -
                       float(clean[f"DAMAGE_{kind}_USD"].sum(min_count=1))) < 0.01
    for column in ["DAMAGE_PROPERTY", "DAMAGE_CROPS"]:
        assert clean.loc[clean[column].isna(), f"{column}_USD"].isna().all()
        assert clean.loc[clean[f"{column}_STATUS"].eq("reported_zero"), f"{column}_USD"].eq(0).all()
    assert len(clean) == len(raw)
    assert file_hash(input_path) == before_hash, "The source file changed during analysis"
    totals = summarize(clean.assign(ALL_RECORDS="all"), ["ALL_RECORDS"]).iloc[0].to_dict()
    report = {
        "run_utc": datetime.now(timezone.utc).isoformat(), "input_name": input_path.name,
        "input_bytes": input_path.stat().st_size, "input_sha256": before_hash,
        "source_vintage_from_supplied_filename": "2025-08-18", "analysis_year": 2024,
        "provenance_note": "Download URL supplied by user; mirror not independently matched to a NOAA original.",
        "sources": SOURCES, "python_version": platform.python_version(), "pandas_version": pd.__version__,
        "matplotlib_version": matplotlib.__version__, "input_records": len(raw), "input_columns": len(raw.columns),
        "output_records": len(clean), "rows_removed": 0, "exact_duplicate_excess_rows": exact_duplicates,
        "duplicate_event_id_rows": duplicate_ids, "distinct_episodes": int(clean.EPISODE_ID.nunique()),
        "distinct_event_types": int(clean.EVENT_TYPE.nunique()), "distinct_state_region_labels": int(clean.STATE.nunique()),
        "unexpected_columns": sorted(set(raw.columns) - set(EXPECTED_COLUMNS)),
        "timezone_label_counts": clean.CZ_TIMEZONE.value_counts().to_dict(),
        "local_begin_min": str(clean.BEGIN_LOCAL_DATETIME.min()), "local_begin_max": str(clean.BEGIN_LOCAL_DATETIME.max()),
        "issue_counts": quality_issues.issue.value_counts().to_dict(),
        "damage_status_counts": {c: clean[f"{c}_STATUS"].value_counts().to_dict() for c in ["DAMAGE_PROPERTY", "DAMAGE_CROPS"]},
        "totals": totals, "verification": "passed: record and damage reconciliation, missing/zero checks, unchanged input hash",
    }
    (out / "quality_report.json").write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")
    print(f"Verified {len(raw):,} records; {clean.EPISODE_ID.nunique():,} episodes; {clean.EVENT_TYPE.nunique()} event types.")
    print(f"Reported property components: ${totals['property_reported_usd_sum']:,.0f}; crop components: ${totals['crops_reported_usd_sum']:,.0f}.")
    print(f"Quality flags: {report['issue_counts']}")
    print(f"Output directory: {out}")


if __name__ == "__main__":
    project = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=project / "noaa_storm_events_2024.csv")
    parser.add_argument("--output-dir", type=Path, default=project / "analysis_outputs")
    args = parser.parse_args()
    run(args.input.resolve(), args.output_dir.resolve())
