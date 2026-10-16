from __future__ import annotations

import importlib
import sys
import types

import pytest


def test_missing_config_gives_friendly_error(monkeypatch):
    for name in ("fal_client", "folder_paths", "torch", "numpy", "requests"):
        try:
            importlib.import_module(name)
        except ImportError:
            monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    try:
        importlib.import_module("PIL.Image")
    except ImportError:
        pil = types.ModuleType("PIL")
        pil.Image = types.ModuleType("PIL.Image")
        monkeypatch.setitem(sys.modules, "PIL", pil)
        monkeypatch.setitem(sys.modules, "PIL.Image", pil.Image)
    monkeypatch.delenv("FAL_KEY", raising=False)
    mod = importlib.import_module("modules.base_fal_api_flux_node")
    monkeypatch.setattr(mod.BaseFalAPIFluxNode, "get_api_key", lambda self: None)
    node = mod.BaseFalAPIFluxNode()  # previously raised TypeError
    with pytest.raises(ValueError, match="API key is not set"):
        node.prepare_arguments("p", 512, 512, 4, 3.5, 1, True)
