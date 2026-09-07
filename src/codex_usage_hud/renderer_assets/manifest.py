"""Fixed-order Renderer asset manifest.

The fragments are deliberately joined without normalization. The assembled
template must remain byte-identical while P6.1 separates the asset source.
"""

from ..config import DEFAULT_COMPOSER_TIKTOKEN_BADGE_ENABLED
from .fragment_01 import HEAD as FRAGMENT_01_HEAD
from .fragment_01 import SHARED_HEAD as FRAGMENT_01_SHARED_HEAD
from .background_usage import TEXT as BACKGROUND_USAGE
from .active_session import TEXT as ACTIVE_SESSION
from .budget import TEXT as BUDGET
from .diagnostics import TEXT as DIAGNOSTICS
from .kernel import TEXT as KERNEL
from .layout import TEXT as LAYOUT
from .router import TEXT as ROUTER
from .model_picker import TEXT as MODEL_PICKER
from .rest_reminder import TEXT as REST_REMINDER
from .session_cleanup import TEXT as SESSION_CLEANUP
from .session_view import TEXT as SESSION_VIEW
from .composer import TEXT as COMPOSER
from .settings_shell import TEXT as SETTINGS_SHELL
from .shared import TEXT as SHARED
from .theme import TEXT as THEME
from .usage_insights import TEXT as USAGE_INSIGHTS

P6_1_TEMPLATE_BYTE_LENGTH = 539347
P6_1_TEMPLATE_SHA256 = "be4417fa105f6809200bf626835f86a35e9b3b7b247f8fca4df84660ad2afbdf"
P6_2_TEMPLATE_BYTE_LENGTH = 559145
P6_2_TEMPLATE_SHA256 = "c7906b5b356a52ec9b7f39da90d893fa6efc362f5be4310b9cddfe93f0ca71de"
P6_3_TEMPLATE_BYTE_LENGTH = 565178
P6_3_TEMPLATE_SHA256 = "90c921761b8ef79d44cc7b91b6afe38079d6a10d29a1ff633802e3b0d21e57fd"
P6_4_TEMPLATE_BYTE_LENGTH = 588947
P6_4_TEMPLATE_SHA256 = "f526946247841fff7d565898326536e49ffdbe89a5c0f214878b1ab602381c79"
P6_5_TEMPLATE_BYTE_LENGTH = 617465
P6_5_TEMPLATE_SHA256 = "49d9654bdcbc45a89369d06807e8dda7b9916f44846a12435808ca4aafde8a1f"
P6_6_TEMPLATE_BYTE_LENGTH = 623485
P6_6_TEMPLATE_SHA256 = "3d022856e438fdf825ec582f099518cfa4917f60fe996856d9630c33858420b5"
P6_7_TEMPLATE_BYTE_LENGTH = 1221529
P6_7_TEMPLATE_SHA256 = "26bb1ba1f8cac5c9aca061ee892ce1fca8e65173210100ef89eb529ce061ff69"

ASSETS = (
    ("00_bootstrap", FRAGMENT_01_HEAD),
    ("00_kernel", KERNEL),
    ("01_shared_head", FRAGMENT_01_SHARED_HEAD),
    ("01_shared", SHARED),
    ("02_model_picker", MODEL_PICKER),
    ("03_theme", THEME),
    ("04_diagnostics", DIAGNOSTICS),
    ("05_budget", BUDGET),
    ("06_rest_reminder", REST_REMINDER),
    ("07_session_view", SESSION_VIEW),
    ("08_usage_insights", USAGE_INSIGHTS),
    ("09_session_cleanup", SESSION_CLEANUP),
    ("10_background_usage", BACKGROUND_USAGE),
    ("11_settings_shell", SETTINGS_SHELL),
    ("12_layout", LAYOUT),
    ("13_composer", COMPOSER),
    ("14_active_session", ACTIVE_SESSION),
    ("15_router", ROUTER),
)

FRAGMENTS = tuple(source for _, source in ASSETS)
ASSET_ORDER = tuple(name for name, _ in ASSETS)

RENDERER_HUD_SCRIPT_TEMPLATE = "".join(FRAGMENTS).replace(
    "__COMPOSER_TIKTOKEN_BADGE_ENABLED__",
    "true" if DEFAULT_COMPOSER_TIKTOKEN_BADGE_ENABLED else "false",
)

__all__ = [
    "ASSETS",
    "ASSET_ORDER",
    "FRAGMENTS",
    "P6_1_TEMPLATE_BYTE_LENGTH",
    "P6_1_TEMPLATE_SHA256",
    "P6_2_TEMPLATE_BYTE_LENGTH",
    "P6_2_TEMPLATE_SHA256",
    "P6_3_TEMPLATE_BYTE_LENGTH",
    "P6_3_TEMPLATE_SHA256",
    "P6_4_TEMPLATE_BYTE_LENGTH",
    "P6_4_TEMPLATE_SHA256",
    "P6_5_TEMPLATE_BYTE_LENGTH",
    "P6_5_TEMPLATE_SHA256",
    "P6_6_TEMPLATE_BYTE_LENGTH",
    "P6_6_TEMPLATE_SHA256",
    "P6_7_TEMPLATE_BYTE_LENGTH",
    "P6_7_TEMPLATE_SHA256",
    "RENDERER_HUD_SCRIPT_TEMPLATE",
]
