"""趋势、排行与每日明细使用同一监控范围权限。"""
from .auth import can_inventory_diagnostics


def dashboard_scope(user, args):
    # 沿用平台唯一超级管理员身份；普通管理员也不获得全站看板权限。
    privileged = can_inventory_diagnostics(user)
    scope = args.get("scope", "all" if privileged else "mine")
    if scope not in {"mine", "all"}:
        raise ValueError("未知监控范围")
    if scope == "all" and not privileged:
        raise PermissionError("仅超级管理员可查看全部目标")
    return scope
