"""Build an editable local Power BI project from verified analysis outputs.

Run analyze_weather.py first, then this script, then open
power_bi/SevereWeather.pbip in Power BI Desktop and refresh the local CSV.
This uses Microsoft's documented PBIP/PBIR and TMSL formats. No service,
account, publishing, external visuals or network connection is used.
Rerunning regenerates the report definition; save manual report edits to
a separate PBIX or a copy of the project before rebuilding.
"""

import csv
import json
from pathlib import Path


BASE = "https://developer.microsoft.com/json-schemas/fabric/"
DEFINITION = BASE + "item/report/definition/"


def write_json(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(content, indent=2, ensure_ascii=False), encoding="utf-8")


def literal(value):
    if isinstance(value, bool):
        text = "true" if value else "false"
    elif isinstance(value, (int, float)):
        text = str(value) + "D"
    else:
        text = "'" + str(value).replace("'", "''") + "'"
    return {"expr": {"Literal": {"Value": text}}}


def field(name, measure=False):
    return {"Measure" if measure else "Column": {
        "Expression": {"SourceRef": {"Entity": "Events"}}, "Property": name}}


def projection(name, measure=False):
    return {"field": field(name, measure), "queryRef": "Events." + name, "nativeQueryRef": name}


def visual(page_dir, name, kind, title, position, roles=None, sort=None, objects=None):
    config = {"visualType": kind,
              "visualContainerObjects": {
                  "title": [{"properties": {"show": literal(True), "text": literal(title),
                                             "fontSize": literal(13), "titleWrap": literal(True)}}],
                  "background": [{"properties": {"show": literal(True), "color": {"solid": {"color": literal("#FFFFFF")}},
                                                  "transparency": literal(0)}}],
                  "border": [{"properties": {"show": literal(True), "color": {"solid": {"color": literal("#DCE4E8")}},
                                              "radius": literal(8)}}],
              }}
    if roles:
        config["query"] = {"queryState": {role: {"projections": values} for role, values in roles.items()}}
        if sort:
            name_sort, is_measure, direction = sort
            config["query"]["sortDefinition"] = {"sort": [{"field": field(name_sort, is_measure), "direction": direction}],
                                                     "isDefaultSort": False}
    if objects:
        config["objects"] = objects
    if kind == "textbox":
        config["visualContainerObjects"]["title"][0]["properties"]["show"] = literal(False)
    if kind == "card":
        config["objects"] = {
            "labels": [{"properties": {"labelDisplayUnits": literal(1),
                                         "labelPrecision": {"expr": {"Literal": {"Value": "2L" if "Coverage" in title else "0L"}}},
                                         "fontSize": literal(28)}}],
            "categoryLabels": [{"properties": {"show": literal(False)}}],
        }
    x, y, width, height = position
    write_json(page_dir / "visuals" / name / "visual.json", {
        "$schema": DEFINITION + "visualContainer/2.7.0/schema.json", "name": name,
        "position": {"x": x, "y": y, "z": 0, "height": height, "width": width, "tabOrder": y + x / 100},
        "visual": config,
    })


def text_box(page_dir, name, text, position, size=16, color="#173B4A"):
    visual(page_dir, name, "textbox", "", position, objects={"general": [{"properties": {"paragraphs": [{
        "textRuns": [{"value": text, "textStyle": {"fontFamily": "Segoe UI", "fontSize": f"{size}pt", "color": color}}]
    }]}}]})


def create_page(report, name, display_name):
    page = report / "definition" / "pages" / name
    write_json(page / "page.json", {"$schema": DEFINITION + "page/1.4.0/schema.json", "name": name,
                                     "displayName": display_name, "displayOption": "FitToPage", "width": 1600, "height": 1100})
    text_box(page, "title", "2024 Severe Weather | " + display_name, (20, 10, 1560, 55), 25)
    text_box(page, "caveat", "Reported event records • August 2025 file vintage • Missing damage stays unknown • Historical reports, not forecasts", (20, 68, 1560, 44), 13)
    for index, (column, title) in enumerate([
        ("State Region", "State / territory / water region"), ("Event Type", "Exact event label"),
        ("Start Month", "Recorded local start month"), ("Marine Context", "Marine context audit flag"),
    ]):
        visual(page, "slicer_" + str(index), "slicer", title, (20 + index * 395, 120, 375, 105),
               {"Values": [projection(column)]}, objects={
                   "data": [{"properties": {"mode": literal("Dropdown")}}],
                   "selection": [{"properties": {"singleSelect": literal(False), "multiSelectWithCTRL": literal(False)}}],
               })
    return page


def build():
    project = Path(__file__).resolve().parent
    source = project / "analysis_outputs" / "events_for_power_bi.csv"
    if not source.is_file():
        raise FileNotFoundError("Run python analyze_weather.py first to create events_for_power_bi.csv")
    with source.open(newline="", encoding="utf-8") as file:
        columns = next(csv.reader(file))
    target = project / "power_bi"
    report = target / "SevereWeather.Report"
    model = target / "SevereWeather.SemanticModel"
    write_json(target / "SevereWeather.pbip", {"$schema": BASE + "pbip/pbipProperties/1.0.0/schema.json",
               "version": "1.0", "artifacts": [{"report": {"path": "SevereWeather.Report"}}],
               "settings": {"enableAutoRecovery": True}})
    write_json(report / "definition.pbir", {"$schema": BASE + "item/report/definitionProperties/2.0.0/schema.json",
               "version": "4.0", "datasetReference": {"byPath": {"path": "../SevereWeather.SemanticModel"}}})
    write_json(report / "definition" / "version.json", {"$schema": DEFINITION + "versionMetadata/1.0.0/schema.json", "version": "2.0.0"})
    write_json(report / "definition" / "report.json", {"$schema": DEFINITION + "report/3.1.0/schema.json", "themeCollection": {}})
    write_json(model / "definition.pbism", {"$schema": BASE + "item/semanticModel/definitionProperties/1.0.0/schema.json", "version": "1.0"})

    money = {"Property USD", "Crop USD", "Complete Damage USD"}
    counts = {"Direct Deaths", "Indirect Deaths", "Direct Injuries", "Indirect Injuries", "Deaths", "Injuries", "Has Begin Coordinates"}
    types = {c: "decimal" if c in money else "int64" if c in counts else "dateTime" if c == "Local Start Date" else "string" for c in columns}
    mtypes = {"decimal": "Currency.Type", "int64": "Int64.Type", "dateTime": "type date", "string": "type text"}
    # Replace blank cells with null before numeric conversion; zeros stay zero.
    mcode = ["let", f'    Source = Csv.Document(File.Contents("{str(source).replace(chr(34), chr(34)*2)}"), [Delimiter=",", Encoding=65001, QuoteStyle=QuoteStyle.Csv]),',
             '    Headers = Table.PromoteHeaders(Source, [PromoteAllScalars=true]),',
             '    Missing = Table.ReplaceValue(Headers, "", null, Replacer.ReplaceValue, Table.ColumnNames(Headers)),',
             '    Typed = Table.TransformColumnTypes(Missing, {' + ", ".join('{"' + c + '", ' + mtypes[types[c]] + '}' for c in columns) + '}, "en-US")',
             "in", "    Typed"]
    measures = {
        "Event Records": ("COUNTROWS('Events')", "#,0"),
        "Episodes": ("DISTINCTCOUNT('Events'[Episode ID])", "#,0"),
        "Reported Property USD": ("SUM('Events'[Property USD])", "$#,0;($#,0);$0"),
        "Reported Crop USD": ("SUM('Events'[Crop USD])", "$#,0;($#,0);$0"),
        "Available Reported USD": ("VAR P = [Reported Property USD] VAR C = [Reported Crop USD] RETURN IF(ISBLANK(P) && ISBLANK(C), BLANK(), COALESCE(P,0) + COALESCE(C,0))", "$#,0;($#,0);$0"),
        "Complete Case Reported USD": ("SUM('Events'[Complete Damage USD])", "$#,0;($#,0);$0"),
        "Property Known Records": ("COUNT('Events'[Property USD])", "#,0"),
        "Crop Known Records": ("COUNT('Events'[Crop USD])", "#,0"),
        "Property Coverage": ("DIVIDE([Property Known Records], [Event Records])", "0.00%"),
        "Crop Coverage": ("DIVIDE([Crop Known Records], [Event Records])", "0.00%"),
        "Property Unknown Records": ("[Event Records] - [Property Known Records]", "#,0"),
        "Crop Unknown Records": ("[Event Records] - [Crop Known Records]", "#,0"),
        "Reported Deaths": ("SUM('Events'[Deaths])", "#,0"),
        "Reported Injuries": ("SUM('Events'[Injuries])", "#,0"),
        "Coordinate Coverage": ("DIVIDE(SUM('Events'[Has Begin Coordinates]), [Event Records])", "0.00%"),
        "Top Event Damage USD": ("IF(RANKX(ALLSELECTED('Events'[Event Type]), [Available Reported USD], , DESC, DENSE) <= 10, [Available Reported USD])", "$#,0"),
        "Top Region Records": ("IF(RANKX(ALLSELECTED('Events'[State Region]), [Event Records], , DESC, DENSE) <= 10, [Event Records])", "#,0"),
    }
    table = {"name": "Events", "columns": [{"name": c, "dataType": types[c], "sourceColumn": c,
              "summarizeBy": "none", **({"formatString": "$#,0"} if c in money else {})} for c in columns],
             "measures": [{"name": name, "expression": expression, "formatString": format_string} for name, (expression, format_string) in measures.items()],
             "partitions": [{"name": "Events", "mode": "import", "source": {"type": "m", "expression": mcode}}]}
    write_json(model / "model.bim", {"name": "SevereWeather", "compatibilityLevel": 1600,
               "model": {"culture": "en-US", "sourceQueryCulture": "en-US", "defaultPowerBIDataSourceVersion": "powerBI_V3",
                         "discourageImplicitMeasures": True, "tables": [table]}})
    (target / "events_import.pq").write_text("\n".join(mcode), encoding="utf-8")
    (target / "measures.dax").write_text("\n\n".join(name + " = " + expression for name, (expression, _) in measures.items()), encoding="utf-8")

    overview = create_page(report, "overview", "Reported impacts")
    for index, name in enumerate(["Event Records", "Reported Property USD", "Reported Crop USD", "Property Coverage"]):
        visual(overview, "card_" + str(index), "card", name, (20 + index * 395, 240, 375, 120), {"Values": [projection(name, True)]})
    visual(overview, "event_damage", "clusteredBarChart", "Available reported damage by event label (top ranks)", (20, 380, 760, 325),
           {"Category": [projection("Event Type")], "Y": [projection("Top Event Damage USD", True)],
            "Tooltips": [projection("Property Coverage", True), projection("Crop Coverage", True), projection("Event Records", True)]},
           ("Top Event Damage USD", True, "Descending"))
    visual(overview, "monthly_damage", "clusteredColumnChart", "Reported property and crop components by local start month", (800, 380, 780, 325),
           {"Category": [projection("Start Month")], "Y": [projection("Reported Property USD", True), projection("Reported Crop USD", True)]}, ("Start Month", False, "Ascending"))
    visual(overview, "region_records", "clusteredBarChart", "Regions with the most reported event records (top ranks)", (20, 725, 760, 280),
           {"Category": [projection("State Region")], "Y": [projection("Top Region Records", True)], "Tooltips": [projection("Property Coverage", True)]}, ("Top Region Records", True, "Descending"))
    visual(overview, "region_coverage", "tableEx", "Compare reported estimates with coverage", (800, 725, 780, 280),
           {"Values": [projection("State Region")] + [projection(n, True) for n in ["Event Records", "Available Reported USD", "Property Coverage", "Crop Coverage"]]}, ("Available Reported USD", True, "Descending"))
    text_box(overview, "footer", "Damage sums include known components only; they are not complete disaster losses. Region totals mix geographic units and reporting coverage. Use the slicers above; click a bar to cross-filter.", (20, 1020, 1560, 65), 12)

    quality = create_page(report, "coverage", "Human impacts & coverage")
    for index, name in enumerate(["Reported Deaths", "Reported Injuries", "Property Unknown Records", "Coordinate Coverage"]):
        visual(quality, "qcard_" + str(index), "card", name, (20 + index * 395, 240, 375, 120), {"Values": [projection(name, True)]})
    visual(quality, "monthly_casualties", "clusteredColumnChart", "Reported direct + indirect casualties by local start month", (20, 380, 760, 325),
           {"Category": [projection("Start Month")], "Y": [projection("Reported Deaths", True), projection("Reported Injuries", True)]}, ("Start Month", False, "Ascending"))
    visual(quality, "event_casualties", "tableEx", "Human impacts by exact event label", (800, 380, 780, 325),
           {"Values": [projection("Event Type")] + [projection(n, True) for n in ["Event Records", "Reported Deaths", "Reported Injuries", "Property Coverage"]]}, ("Reported Deaths", True, "Descending"))
    visual(quality, "geography", "tableEx", "Source geography and coordinate availability", (20, 725, 760, 280),
           {"Values": [projection("Location Type"), projection("Marine Context")] + [projection(n, True) for n in ["Event Records", "Coordinate Coverage", "Property Coverage", "Crop Coverage"]]})
    visual(quality, "damage_status", "tableEx", "Property estimate status: missing is separate from reported zero", (800, 725, 780, 280),
           {"Values": [projection("Property Status")] + [projection(n, True) for n in ["Event Records", "Reported Property USD"]]})
    text_box(quality, "qfooter", "C = county/parish; Z = source zone label. All 2,538 marine-context records were labeled Z, not M. Coordinates omit many zone hazards. Counts are reported records, not unique storms or population-adjusted risk.", (20, 1020, 1560, 65), 12)
    write_json(report / "definition" / "pages" / "pages.json", {"$schema": DEFINITION + "pagesMetadata/1.0.0/schema.json",
               "pageOrder": ["overview", "coverage"], "activePageName": "overview"})
    print("Created local Power BI project:", target / "SevereWeather.pbip")
    print("Open it in Power BI Desktop and refresh. No data is published or shared.")


if __name__ == "__main__":
    build()
