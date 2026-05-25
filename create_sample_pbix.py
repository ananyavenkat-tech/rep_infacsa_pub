"""
Helper: creates a minimal valid PBIX file for multi-file testing.
Produces: input/Sample Sales Report.pbix
"""
import os, json, zipfile, io

layout = {
    "id": 0,
    "resourcePackages": [],
    "sections": [
        {
            "id": 0,
            "name": "ReportSection1",
            "displayName": "Sales Overview",
            "visualContainers": [
                {
                    "id": 0,
                    "x": 0, "y": 0, "z": 0, "width": 400, "height": 300,
                    "config": json.dumps({
                        "singleVisual": {
                            "visualType": "barChart",
                            "prototypeQuery": {
                                "From": [
                                    {"Name": "s", "Entity": "sales_vw", "Type": 0}
                                ],
                                "Select": [
                                    {"Column": {"Expression": {"SourceRef": {"Source": "s"}}, "Property": "region"}, "Name": "Region"},
                                    {"Measure": {"Expression": {"SourceRef": {"Source": "s"}}, "Property": "total_revenue"}, "Name": "Total Revenue"},
                                    {"Aggregation": {"Expression": {"Column": {"Expression": {"SourceRef": {"Source": "s"}}, "Property": "order_count"}}, "Function": 0}, "Name": "Order Count"}
                                ]
                            }
                        }
                    }),
                    "dataTransforms": {
                        "selects": [
                            {
                                "displayName": "Region",
                                "queryName": "sales_vw.region",
                                "expr": {
                                    "Column": {
                                        "Expression": {"SourceRef": {"Entity": "sales_vw"}},
                                        "Property": "region"
                                    }
                                }
                            },
                            {
                                "displayName": "Total Revenue",
                                "queryName": "sales_vw.total_revenue",
                                "expr": {
                                    "Measure": {
                                        "Expression": {"SourceRef": {"Entity": "sales_vw"}},
                                        "Property": "total_revenue"
                                    }
                                }
                            }
                        ]
                    }
                },
                {
                    "id": 1,
                    "x": 400, "y": 0, "z": 0, "width": 400, "height": 300,
                    "config": json.dumps({
                        "singleVisual": {
                            "visualType": "pieChart",
                            "prototypeQuery": {
                                "From": [
                                    {"Name": "c", "Entity": "customer_vw", "Type": 0}
                                ],
                                "Select": [
                                    {"Column": {"Expression": {"SourceRef": {"Source": "c"}}, "Property": "segment"}, "Name": "Segment"},
                                    {"Measure": {"Expression": {"SourceRef": {"Source": "c"}}, "Property": "customer_count"}, "Name": "Customer Count"}
                                ]
                            }
                        }
                    }),
                    "dataTransforms": {
                        "selects": [
                            {
                                "displayName": "Segment",
                                "queryName": "customer_vw.segment",
                                "expr": {
                                    "Column": {
                                        "Expression": {"SourceRef": {"Entity": "customer_vw"}},
                                        "Property": "segment"
                                    }
                                }
                            },
                            {
                                "displayName": "Customer Count",
                                "queryName": "customer_vw.customer_count",
                                "expr": {
                                    "Measure": {
                                        "Expression": {"SourceRef": {"Entity": "customer_vw"}},
                                        "Property": "customer_count"
                                    }
                                }
                            }
                        ]
                    }
                }
            ]
        },
        {
            "id": 1,
            "name": "ReportSection2",
            "displayName": "Customer Detail",
            "visualContainers": [
                {
                    "id": 0,
                    "x": 0, "y": 0, "z": 0, "width": 800, "height": 400,
                    "config": json.dumps({
                        "singleVisual": {
                            "visualType": "tableEx",
                            "prototypeQuery": {
                                "From": [
                                    {"Name": "c", "Entity": "customer_vw", "Type": 0}
                                ],
                                "Select": [
                                    {"Column": {"Expression": {"SourceRef": {"Source": "c"}}, "Property": "customer_name"}, "Name": "Customer Name"},
                                    {"Column": {"Expression": {"SourceRef": {"Source": "c"}}, "Property": "region"}, "Name": "Region"},
                                    {"Measure": {"Expression": {"SourceRef": {"Source": "c"}}, "Property": "lifetime_value"}, "Name": "Lifetime Value"}
                                ]
                            }
                        }
                    }),
                    "dataTransforms": {
                        "selects": [
                            {
                                "displayName": "Customer Name",
                                "queryName": "customer_vw.customer_name",
                                "expr": {
                                    "Column": {
                                        "Expression": {"SourceRef": {"Entity": "customer_vw"}},
                                        "Property": "customer_name"
                                    }
                                }
                            },
                            {
                                "displayName": "Region",
                                "queryName": "customer_vw.region",
                                "expr": {
                                    "Column": {
                                        "Expression": {"SourceRef": {"Entity": "customer_vw"}},
                                        "Property": "region"
                                    }
                                }
                            },
                            {
                                "displayName": "Lifetime Value",
                                "queryName": "customer_vw.lifetime_value",
                                "expr": {
                                    "Measure": {
                                        "Expression": {"SourceRef": {"Entity": "customer_vw"}},
                                        "Property": "lifetime_value"
                                    }
                                }
                            }
                        ]
                    }
                }
            ]
        }
    ]
}

layout_str = json.dumps(layout, ensure_ascii=False)
layout_bytes = layout_str.encode('utf-16-le')

os.makedirs('./input', exist_ok=True)
output_path = './input/Sample Sales Report.pbix'

with zipfile.ZipFile(output_path, 'w', zipfile.ZIP_DEFLATED) as zf:
    zf.writestr('Report/Layout', layout_bytes)

print(f"Created: {output_path}")
print(f"  Sections: {len(layout['sections'])}")
total_visuals = sum(len(s['visualContainers']) for s in layout['sections'])
print(f"  Visuals:  {total_visuals}")
