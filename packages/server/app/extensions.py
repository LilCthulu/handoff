"""Extension loader — the seam between open core and proprietary cloud.

Extensions are discovered via two mechanisms:
1. Python entry_points (group: "handoff.extensions") — for pip-installed packages
2. Config-based explicit import paths (HANDOFF_EXTENSIONS env var) — for dev/override

Each extension module must expose a `register(app: FastAPI) -> None` function
that mounts its routers, middleware, or lifespan hooks onto the app.
"""

import importlib
import os
from importlib.metadata import entry_points

import structlog
from fastapi import FastAPI

logger = structlog.get_logger()


def load_extensions(app: FastAPI) -> list[str]:
    """Discover and register all extensions with the FastAPI app.

    Returns:
        List of loaded extension names.
    """
    loaded: list[str] = []

    # 1. Entry points: pip-installed extensions declare themselves
    loaded.extend(_load_from_entry_points(app))

    # 2. Config: explicit module paths from environment
    loaded.extend(_load_from_config(app))

    if loaded:
        logger.info("extensions_loaded", extensions=loaded)
    else:
        logger.debug("no_extensions_found")

    return loaded


def _load_from_entry_points(app: FastAPI) -> list[str]:
    """Load extensions registered via setuptools entry_points."""
    loaded: list[str] = []

    discovered = entry_points()
    # Python 3.12+ returns a SelectableGroups; 3.9+ supports .select()
    handoff_eps = discovered.select(group="handoff.extensions") if hasattr(discovered, "select") else discovered.get("handoff.extensions", [])

    for ep in handoff_eps:
        try:
            ext_module = ep.load()
            if hasattr(ext_module, "register"):
                ext_module.register(app)
                loaded.append(ep.name)
                logger.info("extension_loaded_entrypoint", name=ep.name, module=ep.value)
            else:
                logger.warning("extension_missing_register", name=ep.name, module=ep.value)
        except Exception:
            logger.exception("extension_load_failed_entrypoint", name=ep.name)

    return loaded


def _load_from_config(app: FastAPI) -> list[str]:
    """Load extensions from the HANDOFF_EXTENSIONS environment variable.

    Format: comma-separated Python module paths.
    Example: HANDOFF_EXTENSIONS=handoff_cloud.routes,handoff_analytics.routes
    """
    loaded: list[str] = []
    ext_config = os.environ.get("HANDOFF_EXTENSIONS", "").strip()

    if not ext_config:
        return loaded

    for module_path in ext_config.split(","):
        module_path = module_path.strip()
        if not module_path:
            continue

        try:
            ext_module = importlib.import_module(module_path)
            if hasattr(ext_module, "register"):
                ext_module.register(app)
                loaded.append(module_path)
                logger.info("extension_loaded_config", module=module_path)
            else:
                logger.warning("extension_missing_register", module=module_path)
        except Exception:
            logger.exception("extension_load_failed_config", module=module_path)

    return loaded
