from __future__ import annotations

import json
from pathlib import Path

import ifcopenshell
from agent.schemas import BuildingPlan
from agent.nodes.build import build
from agent.nodes.validate import validate

storeys = []
elements = []
for floor in range(3):
    ref = f"F{floor:02d}"
    storeys.append({"storey_ref": ref, "name": f"Level {floor + 1}", "elevation": floor * 3.2, "floor_to_floor_height": 3.2})
    corners = [[0, 0], [12, 0], [12, 8], [0, 8]]
    for side in range(4):
        wall = f"{ref}_W{side}"
        elements.append({"element_type": "wall", "storey_ref": ref, "wall_ref": wall, "start_point": corners[side], "end_point": corners[(side + 1) % 4], "height": 3.2, "thickness": 0.2})
        positions = [1.6, 5.0, 8.5] if side % 2 == 0 else [1.6, 5.0]
        for offset in positions:
            elements.append({"element_type": "window", "storey_ref": ref, "host_wall_ref": wall, "distance_along_wall": offset, "width": 2.0, "height": 1.6, "sill_height": 0.9})
    elements.append({"element_type": "slab", "storey_ref": ref, "boundary_points": corners, "depth": 0.2})
elements.append({"element_type": "slab", "storey_ref": "F02", "boundary_points": [[-0.2, -0.2], [12.2, -0.2], [12.2, 8.2], [-0.2, 8.2]], "elevation": 3.2, "depth": 0.2, "slab_type": "ROOF"})
data = {"description": "Synthetic three-storey geometry fixture; hand-authored plan, no model call.", "storeys": storeys, "elements": elements}
Path("workspace/portfolio-plan.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
plan = BuildingPlan.model_validate(data)
state = build({"building_plan": plan, "building_plan_json": data, "job_id": "portfolio-synthetic"})
checked = validate(state)
model = ifcopenshell.open(state["final_ifc_path"])
counts = {kind: len(model.by_type(kind)) for kind in ["IfcBuildingStorey", "IfcWall", "IfcWindow", "IfcSlab"]}
assert counts == {"IfcBuildingStorey": 3, "IfcWall": 12, "IfcWindow": 30, "IfcSlab": 4}, counts
report = {"fixture": "Hand-authored synthetic plan; no LLM call", "counts": counts, "validation": checked["validation_result"]}
Path("workspace/portfolio-result.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
print(json.dumps({"counts": counts, "validation_passed": checked["validation_passed"], "errors": checked["validation_result"].get("all_errors")}))
