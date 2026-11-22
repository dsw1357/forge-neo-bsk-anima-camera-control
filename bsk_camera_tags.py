# SPDX-FileCopyrightText: 2026 灰暗x
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
CameraControlNode - 可视化相机提示词控制节点（重构版）, 参考本节点请注明出处

设计（见 相机控制节点_方案.md）：
1. 方位（Azimuth）由 pos_x 经 az = pos_x * π 分解为 4 个方向分量，按“总和”归一化后瓜分
   方位预算 = azimuth.weight × 极向门控（门控=1 时方位满权重，仅 |pos_y|→1 即完全指向上/下时
   门控→0，前后左右不输出；方位权重只与环绕角度相关，不受上下角度缩放）；
   权重 < azimuth.deadzone_ratio(默认0.2) 的方向不输出。
2. 高度（Elevation，pos_y）权重 = |pos_y| × (1 + extra_master × elevation.extra)，与方位此消彼长
   （pos_y=0 时高度=0，±1 时=1，瓜分 100%）；距离（Distance，pos_z）为 1D 互斥档位、独立于俯仰，
   基础权重恒 1.0，最终权重 = clamp(1 + extra_master × distance.extra, 0.1, 10)。
3. 倾斜（Tilt，roll）超过 tilt.deadzone 时输出 dutch_tag，权重同上。
4. 额外提示词（镜头/景深/运镜/构图/风格）以启用开关控制，可自定义文案。
5. 不输出负向提示词（已删除）。
6. 本节点是提示词的唯一权威组装者；前端 computePrompt 仅作拖拽实时预览，算法须与本文件一致。

注意：roll 作为节点输入由前端画布（Shift+滚轮 / , . 键）写入，使后端能权威计算 tilt。
"""

import json
import math


# ============ 默认配置（必须与前端卡片 DEFAULT_CONFIG 保持一致） ============
DEFAULT_CONFIG = {
    "weight_min": 0.1,
    "weight_max": 10.0,
    "no_weight": False,
    "no_weight_threshold": 0.5,
    "azimuth": {
        "enabled": True,
        "weight": 10.0,
        "deadzone_ratio": 0.2,
        "directions": {
            "front": {"tag": "from front", "enabled": True},
            "back":  {"tag": "from behind", "enabled": True},
            "left":  {"tag": "from right", "enabled": True},
            "right": {"tag": "from left", "enabled": True},
        },
    },
    "elevation": {
        "enabled": True,
        "extra": 10.0,
        "eye_peak": 3.0,
        "categories": {
                "bird": {"tag": "directly above, from above, aerial view,", "enabled": True},
                "high": {"tag": "high angle, from above", "enabled": True},
                "eye":  {"tag": "eye-level", "enabled": True},
                "low":  {"tag": "low angle, from below,", "enabled": True},
                "worm": {"tag": "directly below", "enabled": True},
        },
    },
    "distance": {
        "enabled": True,
        "extra": 0.0,
        "categories": {
            "ecu":    {"tag": "extreme close-up", "enabled": True},
            "cu":     {"tag": "close-up", "enabled": True},
            "medium": {"tag": "medium shot", "enabled": True},
            "full":   {"tag": "full body", "enabled": True},
            "wide":   {"tag": "wide shot", "enabled": True},
        },
    },
    "tilt": {
        "enabled": True,
        "deadzone": 0.15,
        "extra": 0.0,
        "dutch_tag": "dutch angle",
    },
    "extra_master": 1.0,
    "wheel_step": 0.0003,   # 遗留字段：滚轮调距离已废弃（改为右键拖拽），保留以兼容旧配置
    "drag_step": 0.004,     # 右键拖拽调距离：每 1px 改变的 z 量，钳制 [0.0005, 0.02]
    "extras": {
        "lens":        {"enabled": False, "value": "85mm lens"},
        "dof":         {"enabled": False, "value": "shallow depth of field", "weight": 1.3},
        "movement":    {"enabled": False, "value": "handheld camera"},
        "composition": {"enabled": False, "value": "rule of thirds"},
        "style":       {"enabled": False, "value": "cinematic"},
    },
}

DEFAULT_CONFIG_JSON = json.dumps(DEFAULT_CONFIG, ensure_ascii=False)

# 旧 schema 标记键（历史版本遗留，加载时需迁移清除）
LEGACY_KEYS = {"two_tier", "axes", "negative"}

# 距离档位的 z 区间：档内权重随 z 从区间起点(0% 额外权重)线性爬到终点(100% 额外权重)。
# 跨档时下一档从 0% 重新开始（即"跳到下一个提示词同理"）。
DIST_RANGES = {
    "ecu":    (0.7, 1.0),
    "cu":     (0.2, 0.7),
    "medium": (-0.2, 0.2),
    "full":   (-0.7, -0.2),
    "wide":   (-1.0, -0.7),
}

# 中景/全身/远景：距离越远(z 越小)权重越大，故档内 frac 反向计算；
# 特写/近景仍是越近(z 越大)权重越大。
DIST_FAR_STRONGER = {"medium", "full", "wide"}


class CameraControlNode:
    """可视化相机提示词控制节点"""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "pos_x": ("FLOAT", {
                    "default": 0.0, "min": -1.0, "max": 1.0, "step": 0.01,
                    "label": "左右 (X)",
                }),
                "pos_y": ("FLOAT", {
                    "default": 0.0, "min": -1.0, "max": 1.0, "step": 0.01,
                    "label": "上下 (Y)",
                }),
                "pos_z": ("FLOAT", {
                    "default": 0.0, "min": -1.0, "max": 1.0, "step": 0.01,
                    "label": "前后 (Z)",
                }),
                "roll": ("FLOAT", {
                    "default": 0.0, "min": -1.0, "max": 1.0, "step": 0.01,
                    "label": "翻滚 (Roll)",
                }),
                "config": ("STRING", {
                    "multiline": True,
                    "default": DEFAULT_CONFIG_JSON,
                }),
            }
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("相机提示词",)
    FUNCTION = "execute"
    CATEGORY = "bsk_UI/相机"
    DESCRIPTION = "可视化控制相机位置，输出对应的相机提示词（方位 2D 比例分配 + 高度/距离/倾斜 + 镜头/景深/运镜/构图/风格）"

    # ---------- 工具 ----------
    @staticmethod
    def _fmt_weight(w):
        # 两位小数精度，与前端 fmtWeight 一致（始终两位小数占位）
        return f"{round(float(w), 2):.2f}"

    @staticmethod
    def _split_tags(tag):
        """将逗号分隔的多个 tag 拆成列表（去空白、去空）。"""
        return [t.strip() for t in str(tag).split(",") if t.strip()]

    @classmethod
    def _emit_weighted(cls, tag, w):
        """把一个（可能含逗号的）tag 拆成多个 (tag:weight) 片段，同属性多 tag 共享同一权重。"""
        return [f"({t}:{cls._fmt_weight(w)})" for t in cls._split_tags(tag)]

    @classmethod
    def _emit_plain(cls, tag):
        """无权重模式：纯 tag（不带 :权重），多个逗号分隔 tag 各自成段。"""
        return cls._split_tags(tag)

    @staticmethod
    def _merge_defaults(cfg, base):
        """用 base 的默认值递归补齐 cfg 中缺失的字段（兼容旧配置，旧 axes/negative 字段忽略）。"""
        for k, v in base.items():
            if k not in cfg:
                cfg[k] = v
            elif isinstance(v, dict) and isinstance(cfg[k], dict):
                CameraControlNode._merge_defaults(cfg[k], v)
        return cfg

    @classmethod
    def _has_legacy_keys(cls, obj):
        """递归检测是否含旧 schema 标记键（two_tier/axes/negative）。"""
        if not isinstance(obj, dict):
            return False
        for k, v in obj.items():
            if k in LEGACY_KEYS:
                return True
            if isinstance(v, dict) and cls._has_legacy_keys(v):
                return True
        return False

    @classmethod
    def _strip_legacy_keys(cls, obj):
        """递归删除旧 schema 标记键。"""
        if not isinstance(obj, dict):
            return
        for k in list(obj.keys()):
            if k in LEGACY_KEYS:
                del obj[k]
            elif isinstance(obj[k], dict):
                cls._strip_legacy_keys(obj[k])

    @classmethod
    def _load_config(cls, raw):
        """解析 config JSON，失败时回退到默认配置；用默认值补齐缺失字段。
        旧 schema 配置（含 two_tier/axes/negative）做迁移：钳制下限/上限归位到新默认值并清除旧字段，
        避免 weight_min 等旧值把分数权重钳成 1。"""
        if not raw:
            return json.loads(DEFAULT_CONFIG_JSON)
        try:
            cfg = json.loads(raw)
        except Exception:
            cfg = json.loads(DEFAULT_CONFIG_JSON)
        if cls._has_legacy_keys(cfg):
            cfg["weight_min"] = DEFAULT_CONFIG["weight_min"]
            cfg["weight_max"] = DEFAULT_CONFIG["weight_max"]
            cls._strip_legacy_keys(cfg)
        cls._merge_defaults(cfg, json.loads(DEFAULT_CONFIG_JSON))
        return cfg

    # 高度轴是一维互斥轴：平视/俯视/仰视互为反义，不能同时出现（模型会收到冲突信号），
    # 故任何时刻只取一档输出。y>0=鸟瞰（相机在上方俯视），与前端极性一致。
    # eye 是唯一的“中心档”，只占 0 轴及以上：y=0 即完全平视；0 轴以下(y<0)一律是仰视，
    # 全部交给 low/worm，不再用 eye-level 描述——否则“从下往上看”会被说成“平视”。
    ELEV_EYE_MAX = 0.2  # eye 档上界，与 high 档下界一致

    @staticmethod
    def _elevation_key(y):
        if y > 0.7:
            return "bird"
        if y > CameraControlNode.ELEV_EYE_MAX:
            return "high"
        if y >= 0:
            return "eye"
        if y >= -0.7:
            return "low"
        return "worm"

    @classmethod
    def _elevation_weight(cls, cfg, y, ek):
        """高度权重：bird/high/low/worm 是“极端型”档，eye 是“中心型”档，两者单调方向相反，
        不能共用 |y| 公式——那会得出“最平视时 eye-level 权重为 0、完全不输出”的荒谬结果。
        极端型：越偏离水平该档特征越明显，权重随 |y| 单调递增；且权重只依赖 |y|，
                故跨档自动连续（high→bird 共享 from above，切换点不跳变）。
        中心型 eye：y 越接近 0 越平视、权重越大，从 eye_peak(y=0 完全平视) 线性衰减到
                基础权重 1.0(y=ELEV_EYE_MAX，即将进入俯视)，语义方向与权重方向一致。"""
        elev_cfg = cfg.get("elevation") or {}
        if ek == "eye":
            peak = float(elev_cfg.get("eye_peak", 3.0))
            t = max(0.0, min(1.0, float(y) / cls.ELEV_EYE_MAX))
            return peak + (1.0 - peak) * t
        extra_master = float(cfg.get("extra_master", 1.0))
        elev_extra = float(elev_cfg.get("extra", 0.0))
        k = 1.0 + extra_master * elev_extra
        if k <= 0:
            return 0.0  # 额外权重负到抵消基础权重时，高度整体不输出
        return abs(float(y)) * k

    @staticmethod
    def _distance_key(z):
        if z > 0.7:
            return "ecu"
        if z > 0.2:
            return "cu"
        if z >= -0.2:
            return "medium"
        if z >= -0.7:
            return "full"
        return "wide"

    @classmethod
    def _distance_parts(cls, cfg, z):
        """距离权重：档内权重 = clamp(1 + frac × extra_master × distance.extra, weight_min, weight_max)。"""
        key = cls._distance_key(float(z))
        cat = (cfg["distance"].get("categories") or {}).get(key)
        if not cat or not cat.get("tag") or not cat.get("enabled", True):
            return []
        extra_master = float(cfg.get("extra_master", 1.0))
        extra = float(cfg["distance"].get("extra", 0.0))
        wmin = float(cfg.get("weight_min", 0.1))
        wmax = float(cfg.get("weight_max", 10.0))
        start, end = DIST_RANGES[key]
        if key in DIST_FAR_STRONGER:
            frac = max(0.0, min(1.0, (end - float(z)) / (end - start)))  # 越远权重越大
        else:
            frac = max(0.0, min(1.0, (float(z) - start) / (end - start)))  # 越近权重越大
        w = 1.0 + frac * extra_master * extra
        w = min(wmax, max(wmin, w))
        return cls._emit_weighted(cat["tag"], w)

    @classmethod
    def _weighted_tilt(cls, cfg):
        tilt = cfg.get("tilt", {})
        if not tilt.get("enabled", True):
            return []
        extra_master = float(cfg.get("extra_master", 1.0))
        extra = float(tilt.get("extra", 0.0))
        wmax = float(cfg.get("weight_max", 10.0))
        w = 1.0 + extra_master * extra
        w = min(wmax, max(0.1, w))
        return cls._emit_weighted(tilt.get("dutch_tag", ""), w)

    def execute(self, pos_x, pos_y, pos_z, roll, config):
        cfg = self._load_config(config)
        if cfg.get("no_weight"):
            return (self._compute_prompt_no_weight(float(pos_x), float(pos_y), float(pos_z), float(roll), cfg),)
        parts = []

        wmin = float(cfg.get("weight_min", 0.1))
        wmax = float(cfg.get("weight_max", 5.0))
        dz = float(cfg.get("azimuth", {}).get("deadzone_ratio", 0.2))

        # ---------- 方位（Azimuth）2D 比例分配 ----------
        if cfg.get("azimuth", {}).get("enabled", True):
            az = float(pos_x) * math.pi
            front = max(0.0, math.cos(az))
            back = max(0.0, -math.cos(az))
            right = max(0.0, math.sin(az))
            left = max(0.0, -math.sin(az))
            # 按总和归一化：四个方向瓜分 100% 权重（azimuth.weight 为总权重预算）
            s = front + back + left + right
            if s > 0:
                front /= s
                back /= s
                left /= s
                right /= s
            # 方位总预算 = azimuth.weight × 极向门控：方位权重只与环绕角度相关，
            # 基础预算恒为 azimuth.weight（不受上下角度缩放）；仅当相机接近“完全指向上方/下方”
            # (|pos_y|→1) 时水平投影趋零，门控→0，前后左右不输出。
            AZ_POLE = 0.9
            az_gate = max(0.0, min(1.0, (1.0 - abs(float(pos_y))) / (1.0 - AZ_POLE)))
            az_budget = float(cfg["azimuth"]["weight"]) * az_gate
            for name, ratio in (("front", front), ("back", back), ("left", left), ("right", right)):
                dir_cfg = cfg["azimuth"]["directions"].get(name, {})
                if not dir_cfg.get("enabled", True):
                    continue
                w = ratio * az_budget
                if ratio <= 0 or w < dz:
                    continue
                w = min(wmax, max(wmin, w))
                tag = dir_cfg.get("tag", "")
                parts.extend(self._emit_weighted(tag, w))

        # ---------- 高度（Elevation）互斥单档 ----------
        # 权重方向由档类型决定（极端型递增 / 中心型 eye 递减），见 _elevation_weight 说明。
        if cfg.get("elevation", {}).get("enabled", True):
            elev_key = self._elevation_key(float(pos_y))
            elev_cat = (cfg["elevation"].get("categories") or {}).get(elev_key)
            if elev_cat and elev_cat.get("tag") and elev_cat.get("enabled", True):
                # 高度不复用方位死区：方位死区是“方向占比”阈值，套到高度上只会把最接近水平的
                # 那一段（正平视、刚起步的俯仰）整个砍掉，导致 pos_y≈0 时高度词完全缺失。
                ew = self._elevation_weight(cfg, float(pos_y), elev_key)
                if ew > 0:
                    ew = min(wmax, max(wmin, ew))
                    parts.extend(self._emit_weighted(elev_cat["tag"], ew))

        # ---------- 距离（Distance，档内权重随 z 线性爬升） ----------
        if cfg.get("distance", {}).get("enabled", True):
            parts.extend(self._distance_parts(cfg, float(pos_z)))

        # ---------- 倾斜（Tilt） ----------
        if cfg.get("tilt", {}).get("enabled", True):
            if abs(float(roll)) >= float(cfg["tilt"]["deadzone"]):
                parts.extend(self._weighted_tilt(cfg))

        # ---------- 额外相机提示词 ----------
        extras = cfg.get("extras", {})
        for key in ("lens", "dof", "movement", "composition", "style"):
            e = extras.get(key)
            if not e or not e.get("enabled"):
                continue
            val = (e.get("value") or "").strip()
            if not val:
                continue
            if key == "dof":
                w = e.get("weight", 1.3)
                parts.append(f"({val}:{self._fmt_weight(w)})")
            else:
                parts.append(val)

        result = ", ".join(parts)
        if result:
            result += ","
        return (result,)

    @classmethod
    def _compute_prompt_no_weight(cls, pos_x, pos_y, pos_z, roll, cfg):
        """无权重模式：纯 tag 输出（不带 :权重），兼容不支持提示词权重的模型。
        方位只输出主导方向；次要方向仅当其占比 >= 阈值才输出（阈值可调，默认 0.5=中线切换）。
        高度/距离按档位输出，默认档(eye/medium)不输出。"""
        parts = []
        thr = float(cfg.get("no_weight_threshold", 0.5))
        az = cfg.get("azimuth", {})
        if az.get("enabled", True):
            a = float(pos_x) * math.pi
            front = max(0.0, math.cos(a))
            back = max(0.0, -math.cos(a))
            right = max(0.0, math.sin(a))
            left = max(0.0, -math.sin(a))
            s = front + back + left + right
            if s > 0:
                front /= s
                back /= s
                left /= s
                right /= s
            AZ_POLE = 0.9
            az_gate = max(0.0, min(1.0, (1.0 - abs(float(pos_y))) / (1.0 - AZ_POLE)))
            if az_gate > 0:
                dirs = (("front", front), ("back", back), ("left", left), ("right", right))
                dom = None
                dom_r = -1.0
                for name, ratio in dirs:
                    d = az.get("directions", {}).get(name, {})
                    if not d.get("enabled", True):
                        continue
                    if ratio > dom_r:
                        dom_r = ratio
                        dom = name
                if dom is not None and dom_r > 0:
                    parts.extend(cls._emit_plain(az["directions"][dom].get("tag", "")))
                for name, ratio in dirs:
                    if name == dom:
                        continue
                    d = az.get("directions", {}).get(name, {})
                    if not d.get("enabled", True):
                        continue
                    if ratio >= thr:
                        parts.extend(cls._emit_plain(d.get("tag", "")))
        # 高度（eye 默认平视档不输出）
        if cfg.get("elevation", {}).get("enabled", True):
            ek = cls._elevation_key(float(pos_y))
            if ek != "eye":
                ecat = (cfg["elevation"].get("categories") or {}).get(ek)
                if ecat and ecat.get("tag") and ecat.get("enabled", True):
                    parts.extend(cls._emit_plain(ecat["tag"]))
        # 距离（medium 默认中景档不输出）
        if cfg.get("distance", {}).get("enabled", True):
            dk = cls._distance_key(float(pos_z))
            if dk != "medium":
                dcat = (cfg["distance"].get("categories") or {}).get(dk)
                if dcat and dcat.get("tag") and dcat.get("enabled", True):
                    parts.extend(cls._emit_plain(dcat["tag"]))
        # 倾斜（纯 tag）
        if cfg.get("tilt", {}).get("enabled", True) and abs(float(roll)) >= float(cfg["tilt"]["deadzone"]):
            parts.extend(cls._emit_plain(cfg["tilt"].get("dutch_tag", "")))
        # 额外提示词（dof 也走纯 tag）
        extras = cfg.get("extras", {})
        for key in ("lens", "dof", "movement", "composition", "style"):
            e = extras.get(key)
            if not e or not e.get("enabled"):
                continue
            val = (e.get("value") or "").strip()
            if val:
                parts.append(val)
        result = ", ".join(parts)
        return result + "," if result else ""


NODE_CLASS_MAPPINGS = {
    "CameraControlNode": CameraControlNode,
}
NODE_DISPLAY_NAME_MAPPINGS = {
    "CameraControlNode": "BSK_相机控制",
}
