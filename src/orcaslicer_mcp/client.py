from __future__ import annotations
import asyncio
import json
import math
import httpx
import websockets
from .config import Config
from .errors import error_from_status, NotReachable, ApiError, UiTimeout


def _plate_params(plate: int | None) -> dict | None:
    """Query parameters that address a plate by index; None leaves the fork on the current one."""
    return {"plate": plate} if plate is not None else None


class OrcaClient:
    def __init__(self, cfg: Config):
        self._cfg = cfg
        self._http = httpx.AsyncClient(
            base_url=cfg.base_url,
            headers={"X-Api-Token": cfg.token},
            timeout=cfg.timeout,
        )

    async def __aenter__(self) -> "OrcaClient":
        return self

    async def __aexit__(self, *exc) -> None:
        await self._http.aclose()

    async def _request(self, method: str, path: str, *, json=None, params=None,
                       timeout: float | None = None) -> dict:
        try:
            resp = await self._http.request(
                method, path, json=json, params=params,
                timeout=timeout if timeout is not None else httpx.USE_CLIENT_DEFAULT)
        except httpx.TimeoutException as e:
            raise UiTimeout(f"OrcaSlicer did not respond in time: {e}") from e
        except httpx.TransportError as e:
            raise NotReachable(f"OrcaSlicer not reachable at {self._cfg.base_url}: {e}") from e
        if resp.status_code >= 400:
            try:
                body = resp.json()
            except Exception:
                body = {}
            raise error_from_status(resp.status_code, body)
        return resp.json()

    async def get_status(self) -> dict:
        data = await self._request("GET", "/api/v1/status")
        # F4: the fork emits presets.filaments (plural), but the rest of the API
        # surface is singular (type=filament, /presets keys, modified.filament).
        # Normalize here so callers see one convention (it holds a list either way).
        presets = data.get("presets")
        if isinstance(presets, dict) and "filaments" in presets:
            presets["filament"] = presets.pop("filaments")
        return data

    async def get_objects(self) -> dict:
        data = await self._request("GET", "/api/v1/objects")
        # F5: transform_object takes rotate in DEGREES, but the fork reports
        # transform.rotation in RADIANS. Convert the readback to degrees so a
        # set-then-read round-trip is consistent. (placement uses size_mm, not
        # rotation, so this is safe for internal consumers.)
        for obj in data.get("objects") or []:
            rot = obj.get("transform", {}).get("rotation")
            if isinstance(rot, list):
                obj["transform"]["rotation"] = [math.degrees(v) for v in rot]
        return data

    async def delete_object(self, obj_id: int) -> dict:
        return await self._request("DELETE", f"/api/v1/objects/{obj_id}")

    async def duplicate_object(self, obj_id: int) -> dict:
        return await self._request("POST", f"/api/v1/objects/{obj_id}/duplicate")

    async def set_object_config(self, obj_id: int, changes: dict) -> dict:
        return await self._request("PUT", f"/api/v1/objects/{obj_id}/config", json=changes)

    async def transform_object(self, obj_id: int, translate=None, rotate=None, scale=None,
                               instance: int | None = None, plate: int | None = None) -> dict:
        body = {}
        if instance is not None:
            body["instance"] = instance
        if plate is not None:
            body["plate"] = plate
        if translate is not None:
            body["translate"] = translate
        if rotate is not None:
            body["rotate"] = rotate
        if scale is not None:
            body["scale"] = scale
        return await self._request("POST", f"/api/v1/objects/{obj_id}/transform", json=body)

    async def arrange(self, plate: int | None = None) -> dict:
        return await self._request("POST", "/api/v1/arrange", params=_plate_params(plate))

    async def orient(self, plate: int | None = None) -> dict:
        return await self._request("POST", "/api/v1/orient", params=_plate_params(plate))

    async def job_status(self) -> dict:
        return await self._request("GET", "/api/v1/jobs/status")

    async def get_config(self, keys: list[str] | None) -> dict:
        # The fork's /config `keys` filter splits the RAW query string on ',' without
        # URL-decoding first, so httpx's percent-encoded comma (%2C) matches nothing and
        # returns {}. Rather than depend on that fragile path, fetch the full config
        # (~20KB on LAN) and filter locally — parser-independent. See test_client_config.
        data = await self._request("GET", "/api/v1/config")
        cfg = data.get("config", {})
        if keys:
            return {k: cfg[k] for k in keys if k in cfg}
        return cfg

    async def put_config(self, changes: dict) -> dict:
        return await self._request("PUT", "/api/v1/config", json=changes)

    async def slice(self, plate: int | None = None) -> dict:
        return await self._request("POST", "/api/v1/slice", params=_plate_params(plate))

    async def slice_status(self) -> dict:
        return await self._request("GET", "/api/v1/slice/status")

    async def cancel_slice(self) -> dict:
        return await self._request("POST", "/api/v1/slice/cancel")

    async def load_model(self, path: str) -> dict:
        # STEP tessellation can far exceed the default timeout (the fork holds
        # the request up to 120 s); give the read a matching allowance.
        return await self._request("POST", "/api/v1/model", json={"path": path},
                                   timeout=130.0)

    async def select_preset(self, ptype: str, name: str) -> dict:
        return await self._request("PUT", "/api/v1/preset", json={"type": ptype, "name": name})

    async def save_preset(self, ptype: str, name: str, detach: bool = False) -> dict:
        return await self._request("POST", "/api/v1/preset/save",
                                   json={"type": ptype, "name": name, "detach": detach})

    async def get_presets(self) -> dict:
        return await self._request("GET", "/api/v1/presets")

    async def get_preset_config(self, ptype: str, name: str) -> dict:
        return await self._request("POST", "/api/v1/preset/config", json={"type": ptype, "name": name})

    async def delete_preset(self, ptype: str, name: str) -> dict:
        return await self._request("DELETE", "/api/v1/preset", json={"type": ptype, "name": name})

    async def set_layer_height(self, obj_id: int, mode: str, quality: float = 0.5) -> dict:
        body = {"mode": mode}
        if mode == "adaptive":
            body["quality"] = quality
        return await self._request("PUT", f"/api/v1/objects/{obj_id}/layer_height", json=body)

    async def set_height_range(self, obj_id: int, min_z: float | None = None,
                               max_z: float | None = None, layer_height: float | None = None,
                               clear: bool = False) -> dict:
        body = {"clear": True} if clear else {"min_z": min_z, "max_z": max_z, "layer_height": layer_height}
        return await self._request("PUT", f"/api/v1/objects/{obj_id}/height_range", json=body)

    async def get_gcode(self, plate: int | None = None) -> bytes:
        try:
            resp = await self._http.get("/api/v1/gcode", params=_plate_params(plate))
        except httpx.TimeoutException as e:
            raise UiTimeout(f"OrcaSlicer did not respond in time: {e}") from e
        except httpx.TransportError as e:
            raise NotReachable(f"OrcaSlicer not reachable at {self._cfg.base_url}: {e}") from e
        if resp.status_code >= 400:
            try:
                body = resp.json()
            except Exception:
                body = {}
            raise error_from_status(resp.status_code, body)
        return resp.content

    async def get_plate_render(self, view: str = "editor", angle: str = "iso",
                               width: int = 800, height: int = 600,
                               frame: str | None = None, plate: int | None = None) -> bytes:
        params: dict[str, str | int] = {
            "view": view, "angle": angle, "width": width, "height": height}
        if frame is not None:
            params["frame"] = frame
        if plate is not None:
            params["plate"] = plate
        try:
            resp = await self._http.get("/api/v1/plate/render", params=params)
        except httpx.TimeoutException as e:
            raise UiTimeout(f"OrcaSlicer did not respond in time: {e}") from e
        except httpx.TransportError as e:
            raise NotReachable(f"OrcaSlicer not reachable at {self._cfg.base_url}: {e}") from e
        if resp.status_code >= 400:
            try:
                body = resp.json()
            except Exception:
                body = {}
            raise error_from_status(resp.status_code, body)
        return resp.content

    # --- plates ---------------------------------------------------------------

    async def list_plates(self) -> dict:
        return await self._request("GET", "/api/v1/plates")

    async def get_plate(self, index: int) -> dict:
        return await self._request("GET", f"/api/v1/plates/{index}")

    async def add_plate(self, name: str | None = None) -> dict:
        return await self._request("POST", "/api/v1/plates",
                                   json={"name": name} if name is not None else {})

    async def duplicate_plate(self, index: int) -> dict:
        return await self._request("POST", f"/api/v1/plates/{index}/duplicate")

    async def select_plate(self, index: int) -> dict:
        return await self._request("POST", f"/api/v1/plates/{index}/select")

    async def update_plate(self, index: int, changes: dict) -> dict:
        return await self._request("PUT", f"/api/v1/plates/{index}", json=changes)

    async def delete_plate(self, index: int, force: bool = False) -> dict:
        return await self._request("DELETE", f"/api/v1/plates/{index}",
                                   params={"force": "true"} if force else None)

    # --- project --------------------------------------------------------------
    # The fork holds save/open for up to 120 s and new for 60 s; give the read a margin.

    async def save_project(self, path: str | None = None) -> dict:
        return await self._request("POST", "/api/v1/project/save",
                                   json={"path": path} if path else {}, timeout=130.0)

    async def open_project(self, path: str, discard: bool = False) -> dict:
        return await self._request("POST", "/api/v1/project/open",
                                   json={"path": path, "discard": discard}, timeout=130.0)

    async def new_project(self, discard: bool = False) -> dict:
        return await self._request("POST", "/api/v1/project/new",
                                   json={"discard": discard}, timeout=70.0)

    def _ws_url(self) -> str:
        base = self._cfg.base_url
        ws = base.replace("https://", "wss://", 1).replace("http://", "ws://", 1)
        return f"{ws}/api/v1/events?token={self._cfg.token}"

    async def collect_events(self, seconds: float, stop_on: set[str] | None = None) -> list[dict]:
        events: list[dict] = []
        deadline = asyncio.get_event_loop().time() + seconds
        try:
            async with websockets.connect(self._ws_url()) as ws:
                while True:
                    remaining = deadline - asyncio.get_event_loop().time()
                    if remaining <= 0:
                        break
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
                    except asyncio.TimeoutError:
                        break
                    try:
                        evt = json.loads(raw)
                    except (ValueError, TypeError):
                        continue
                    if not isinstance(evt, dict):
                        continue
                    events.append(evt)
                    if stop_on and evt.get("event") in stop_on:
                        break
        except Exception:
            # A closed/failed WS (e.g. bad token) just yields what we have.
            pass
        return events
