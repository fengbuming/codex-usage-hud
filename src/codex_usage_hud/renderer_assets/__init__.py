"""Ordered Renderer JavaScript/CSS asset fragments."""

__all__ = ["RENDERER_HUD_SCRIPT_TEMPLATE"]


def __getattr__(name: str):
    # The desktop bubble imports only speed_titles for its shared vehicle art.
    # Importing that leaf must not assemble the entire renderer JavaScript/CSS.
    if name == "RENDERER_HUD_SCRIPT_TEMPLATE":
        from .manifest import RENDERER_HUD_SCRIPT_TEMPLATE

        globals()[name] = RENDERER_HUD_SCRIPT_TEMPLATE
        return RENDERER_HUD_SCRIPT_TEMPLATE
    raise AttributeError(name)
