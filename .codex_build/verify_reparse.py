"""临时端到端验证：用改造后的 parse_rollout 重解析真实 rollout，
对比清洗前后 tool_text 是否仍含目标短语。用完即删。
"""

from __future__ import annotations

import sys
from pathlib import Path

SRC = Path(r"E:\Project\codex-usage-hud\src")
sys.path.insert(0, str(SRC))

from codex_usage_hud.core.session_search import parse_rollout  # noqa: E402

SESSIONS = {
    "01a06bb8-3ad7-75a2-9e25-328271c8bb82": "CDP 整页快照(body)",
    "01a079b9-1793-7660-a68b-df8350cb7ed6": "a11y 树(button ref)",
    "01a07b3f-4982-73f3-8a85-bfafc9a83135": "read_thread(preview)",
    "01a064e6-7975-7913-a3a1-4266544b12cf": "真实正文(须保住)",
}
Q = "点击没有反应"
SESSIONS_ROOT = Path(r"C:\Users\zjxqm\.codex\sessions")


def rollouts_for(sid: str) -> list[Path]:
    return sorted(SESSIONS_ROOT.rglob(f"*{sid}*.jsonl"))


def main() -> None:
    for sid, kind in SESSIONS.items():
        paths = rollouts_for(sid)
        if not paths:
            print(f"[跳过] {sid} 未找到 rollout")
            continue
        user, assistant, tool, _changed = parse_rollout(paths)
        hit_tool = Q in tool
        hit_body = Q in user or Q in assistant
        must_keep = "须保住" in kind
        ok = (not hit_tool) or (must_keep and hit_body)
        flag = "OK" if (hit_body or not hit_tool) else "仍污染"
        print(f"[{flag}] {sid}  [{kind}]")
        print(f"     rollout 分片 {len(paths)} 个")
        print(f"     tool_text  含短语: {hit_tool}")
        print(f"     user/assistant 含短语: {hit_body}")
        print(f"     tool_text 长度 {len(tool)}")
        print()


if __name__ == "__main__":
    main()
