"""Plate and project tools, and the `plate` argument on the tools that predate plates."""
import json

import httpx
import respx

import orcaslicer_mcp.server as srv

B = "http://x:13130"


def _env(m):
    m.setenv("ORCA_API_TOKEN", "tok"); m.setenv("ORCA_API_URL", B)


PLATE = {"index": 1, "name": "Clips", "current": True, "locked": False, "empty": False,
         "bed_type": "default", "print_sequence": "by object", "objects": []}


@respx.mock
async def test_list_plates(monkeypatch):
    _env(monkeypatch)
    respx.get(f"{B}/api/v1/plates").mock(return_value=httpx.Response(
        200, json={"count": 2, "current": 0, "plates": [PLATE]}))
    out = await srv.list_plates()
    assert out["count"] == 2 and out["plates"][0]["name"] == "Clips"


@respx.mock
async def test_get_plate_unknown_keeps_count(monkeypatch):
    _env(monkeypatch)
    respx.get(f"{B}/api/v1/plates/9").mock(return_value=httpx.Response(
        404, json={"error": "unknown_plate", "count": 2}))
    out = await srv.get_plate(9)
    assert out == {"error": "unknown_plate", "count": 2}


@respx.mock
async def test_select_plate_busy(monkeypatch):
    _env(monkeypatch)
    respx.post(f"{B}/api/v1/plates/1/select").mock(return_value=httpx.Response(
        409, json={"error": "busy_slicing"}))
    assert (await srv.select_plate(1))["error"] == "busy_slicing"


@respx.mock
async def test_add_plate_sends_name(monkeypatch):
    _env(monkeypatch)
    route = respx.post(f"{B}/api/v1/plates").mock(return_value=httpx.Response(201, json=PLATE))
    out = await srv.add_plate("Clips")
    assert out["index"] == 1
    assert json.loads(route.calls.last.request.content) == {"name": "Clips"}


@respx.mock
async def test_duplicate_plate(monkeypatch):
    _env(monkeypatch)
    respx.post(f"{B}/api/v1/plates/0/duplicate").mock(return_value=httpx.Response(201, json=PLATE))
    assert (await srv.duplicate_plate(0))["index"] == 1


@respx.mock
async def test_set_plate_settings_sends_only_given_fields(monkeypatch):
    _env(monkeypatch)
    route = respx.put(f"{B}/api/v1/plates/1").mock(return_value=httpx.Response(200, json=PLATE))
    await srv.set_plate_settings(1, bed_type="Textured PEI Plate", first_layer_sequence=[2],
                                 spiral_mode="default")
    assert json.loads(route.calls.last.request.content) == {
        "bed_type": "Textured PEI Plate", "first_layer_sequence": [2], "spiral_mode": "default"}


@respx.mock
async def test_set_plate_settings_keeps_per_field_errors(monkeypatch):
    _env(monkeypatch)
    respx.put(f"{B}/api/v1/plates/1").mock(return_value=httpx.Response(
        422, json={"error": "invalid_settings", "errors": {"bed_type": "expected a bed type name"}}))
    out = await srv.set_plate_settings(1, bed_type="Nope")
    assert out["error"] == "invalid_settings"
    assert out["errors"] == {"bed_type": "expected a bed type name"}


async def test_set_plate_settings_needs_a_change(monkeypatch):
    _env(monkeypatch)
    assert (await srv.set_plate_settings(1))["error"] == "nothing_to_change"


@respx.mock
async def test_delete_plate_not_empty_lists_objects(monkeypatch):
    _env(monkeypatch)
    objs = [{"id": 7, "name": "clip", "instance": 0}]
    route = respx.delete(f"{B}/api/v1/plates/1").mock(return_value=httpx.Response(
        409, json={"error": "plate_not_empty", "objects": objs}))
    out = await srv.delete_plate(1)
    assert out["error"] == "plate_not_empty" and out["objects"] == objs
    assert "force" not in str(route.calls.last.request.url)


@respx.mock
async def test_delete_plate_force(monkeypatch):
    _env(monkeypatch)
    route = respx.delete(f"{B}/api/v1/plates/1").mock(return_value=httpx.Response(
        200, json={"deleted": 1, "moved_objects": [], "count": 1, "current": 0}))
    assert (await srv.delete_plate(1, force=True))["deleted"] == 1
    assert route.calls.last.request.url.params["force"] == "true"


@respx.mock
async def test_move_to_plate_sends_instance_and_plate(monkeypatch):
    _env(monkeypatch)
    route = respx.post(f"{B}/api/v1/objects/42/transform").mock(return_value=httpx.Response(
        200, json={"id": 42, "instance": 2, "plate": 1, "transform": {}}))
    out = await srv.move_to_plate(42, plate=1, instance=2)
    assert out["plate"] == 1
    assert json.loads(route.calls.last.request.content) == {"instance": 2, "plate": 1}


@respx.mock
async def test_transform_object_passes_plate(monkeypatch):
    _env(monkeypatch)
    route = respx.post(f"{B}/api/v1/objects/42/transform").mock(return_value=httpx.Response(
        200, json={"id": 42, "plate": 0, "transform": {}}))
    await srv.transform_object(42, translate=[1, 0, 0], plate=0)
    assert json.loads(route.calls.last.request.content) == {"plate": 0, "translate": [1, 0, 0]}


@respx.mock
async def test_slice_passes_plate_query(monkeypatch):
    _env(monkeypatch)
    route = respx.post(f"{B}/api/v1/slice").mock(return_value=httpx.Response(
        202, json={"started": True, "plate": 1}))
    assert (await srv.slice(plate=1))["plate"] == 1
    assert route.calls.last.request.url.params["plate"] == "1"


@respx.mock
async def test_slice_without_plate_sends_no_query(monkeypatch):
    _env(monkeypatch)
    route = respx.post(f"{B}/api/v1/slice").mock(return_value=httpx.Response(202, json={"started": True}))
    await srv.slice()
    assert "plate" not in route.calls.last.request.url.params


@respx.mock
async def test_slice_and_wait_surfaces_the_real_validation_message(monkeypatch):
    _env(monkeypatch)
    body = {"error": "slice_not_started", "plate": 1, "reason": "validation",
            "message": "Lid Clip.stl is too close to others, and collisions may be caused.",
            "object": "Lid Clip.stl"}
    route = respx.post(f"{B}/api/v1/slice").mock(return_value=httpx.Response(422, json=body))
    out = await srv.slice_and_wait(timeout=5, plate=1)
    assert out["error"] == "slice_not_started"
    assert out["message"].startswith("Lid Clip.stl is too close")
    assert out["reason"] == "validation" and out["object"] == "Lid Clip.stl"
    assert route.call_count == 1  # an explained refusal is not retried


@respx.mock
async def test_slice_all_plates_skips_empty_and_reports_each(monkeypatch):
    _env(monkeypatch)
    plates = [{"index": 0, "empty": False}, {"index": 1, "empty": True}, {"index": 2, "empty": False}]
    respx.get(f"{B}/api/v1/plates").mock(return_value=httpx.Response(200, json={"plates": plates}))

    def slice_side_effect(request):
        if request.url.params["plate"] == "2":
            return httpx.Response(422, json={"error": "slice_not_started", "reason": "outside",
                                             "message": "An object is partly outside the plate"})
        return httpx.Response(202, json={"started": True, "plate": 0})
    respx.post(f"{B}/api/v1/slice").mock(side_effect=slice_side_effect)
    respx.get(f"{B}/api/v1/slice/status").mock(return_value=httpx.Response(
        200, json={"state": "done", "percent": 100, "message": "", "plate": 0,
                   "stats": {"estimated_time": "1m"}, "warnings": []}))
    out = await srv.slice_all_plates(timeout_per_plate=5)
    by_plate = {r["plate"]: r for r in out["plates"]}
    assert by_plate[1]["skipped"] == "empty"
    assert "error" not in by_plate[0]
    assert by_plate[2]["error"] == "slice_not_started" and by_plate[2]["reason"] == "outside"


@respx.mock
async def test_get_gcode_for_plate(monkeypatch):
    _env(monkeypatch)
    route = respx.get(f"{B}/api/v1/gcode").mock(return_value=httpx.Response(200, content=b"G28\n"))
    out = await srv.get_gcode(plate=1)
    assert out["bytes"] == 4
    assert route.calls.last.request.url.params["plate"] == "1"


@respx.mock
async def test_arrange_plate_locked(monkeypatch):
    _env(monkeypatch)
    route = respx.post(f"{B}/api/v1/arrange").mock(return_value=httpx.Response(
        409, json={"error": "plate_locked"}))
    assert (await srv.arrange_plate(plate=0))["error"] == "plate_locked"
    assert route.calls.last.request.url.params["plate"] == "0"


@respx.mock
async def test_render_plate_passes_plate(monkeypatch):
    _env(monkeypatch)
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 16
    route = respx.get(f"{B}/api/v1/plate/render").mock(return_value=httpx.Response(200, content=png))
    await srv.render_plate(plate=1)
    assert route.calls.last.request.url.params["plate"] == "1"


@respx.mock
async def test_save_project_with_path(monkeypatch):
    _env(monkeypatch)
    route = respx.post(f"{B}/api/v1/project/save").mock(return_value=httpx.Response(
        200, json={"saved": "C:/p/x.3mf"}))
    assert (await srv.save_project("C:/p/x.3mf"))["saved"] == "C:/p/x.3mf"
    assert json.loads(route.calls.last.request.content) == {"path": "C:/p/x.3mf"}


@respx.mock
async def test_save_project_never_saved(monkeypatch):
    _env(monkeypatch)
    respx.post(f"{B}/api/v1/project/save").mock(return_value=httpx.Response(
        409, json={"error": "no_project_path", "detail": "the project was never saved; pass a path"}))
    out = await srv.save_project()
    assert out["error"] == "no_project_path" and "pass a path" in out["detail"]


@respx.mock
async def test_open_project_dirty_explains_what(monkeypatch):
    _env(monkeypatch)
    route = respx.post(f"{B}/api/v1/project/open").mock(return_value=httpx.Response(
        409, json={"error": "project_dirty", "model_dirty": True, "presets_dirty": False}))
    out = await srv.open_project("C:/p/x.3mf")
    assert out["error"] == "project_dirty" and out["model_dirty"] is True
    assert json.loads(route.calls.last.request.content) == {"path": "C:/p/x.3mf", "discard": False}


@respx.mock
async def test_new_project_discard(monkeypatch):
    _env(monkeypatch)
    route = respx.post(f"{B}/api/v1/project/new").mock(return_value=httpx.Response(
        200, json={"created": True, "plate_count": 1}))
    assert (await srv.new_project(discard=True))["created"] is True
    assert json.loads(route.calls.last.request.content) == {"discard": True}
