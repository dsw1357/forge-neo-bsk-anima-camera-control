# SPDX-License-Identifier: AGPL-3.0-or-later

import json

import gradio as gr

from backend.diffusion_engine.anima import Anima
from bsk_camera_tags import DEFAULT_CONFIG, DIST_RANGES, CameraControlNode
from modules import extra_networks, scripts
from modules.ui_components import InputAccordion


LORA_NAME = "BSKAnimaCamera-v1.0"
DEFAULT_STRENGTH = 0.8
ORBIT_VIEW = """
<div class="bsk-orbit" data-bsk-orbit>
    <div class="bsk-orbit__header">
        <div>
            <div class="bsk-orbit__title">카메라 위치</div>
            <output class="bsk-orbit__position" data-bsk-position></output>
        </div>
        <button class="bsk-orbit__reset" type="button" data-bsk-reset>초기화</button>
    </div>
    <canvas class="bsk-orbit__canvas" tabindex="0" aria-label="카메라 위치 조절"
            aria-describedby="bsk-orbit-help bsk-orbit-keyboard"></canvas>
    <div class="bsk-orbit__help" id="bsk-orbit-help">
        <span><strong>드래그</strong> 방향·높이</span>
        <span><strong>휠</strong> 거리</span>
        <span><strong>Shift+휠</strong> 기울기</span>
    </div>
    <span class="bsk-orbit__sr" id="bsk-orbit-keyboard">
        화살표로 방향과 높이, 더하기와 빼기로 거리, 대괄호 키로 기울기를 조절합니다.
    </span>
</div>
"""


def wide_shot_progress(distance):
    far, near = DIST_RANGES["wide"]
    return max(0.0, min(1.0, (near - distance) / (near - far)))


class ForgeCameraControlNode(CameraControlNode):
    @classmethod
    def _distance_parts(cls, cfg, z):
        category = cfg["distance"]["categories"].get(cls._distance_key(z), {})
        tag = category.get("tag")
        if not tag or not category.get("enabled", True):
            return []

        weight = 1.0 + 4.0 * min(abs(z), 1.0) + 7.0 * wide_shot_progress(z)
        return cls._emit_weighted(tag, weight)


CAMERA = ForgeCameraControlNode()


def camera_tags(azimuth, elevation, distance, roll, plain):
    z = distance / 100
    config = {"no_weight": plain}
    progress = wide_shot_progress(z)
    if progress and not plain:
        # At the far end, let the framing cue outweigh the angle cues.
        azimuth_scale = 1.0 - 0.9 * progress
        elevation_scale = 1.0 - (2.0 / 3.0) * progress
        azimuth_defaults = DEFAULT_CONFIG["azimuth"]
        elevation_defaults = DEFAULT_CONFIG["elevation"]
        config["azimuth"] = {
            "weight": azimuth_defaults["weight"] * azimuth_scale,
            "deadzone_ratio": azimuth_defaults["deadzone_ratio"] * azimuth_scale,
        }
        config["elevation"] = {
            "eye_peak": elevation_defaults["eye_peak"] * elevation_scale,
            "extra": (1.0 + elevation_defaults["extra"]) * elevation_scale - 1.0,
        }
    tags, = CAMERA.execute(azimuth / 180, elevation / 90, z, roll / 180, json.dumps(config))
    return tags.rstrip(", ")


def apply_camera(prompt, tags, strength):
    text, networks = extra_networks.parse_prompt(prompt)
    updated = prompt.strip()
    if tags and not text.rstrip(" ,\n").endswith(tags):
        updated = ", ".join(part for part in (updated.rstrip(" ,"), tags) if part)
    has_lora = any(params.positional and params.positional[0].casefold() == LORA_NAME.casefold()
                   for params in networks["lora"])
    if strength > 0 and not has_lora:
        updated = f"{updated} <lora:{LORA_NAME}:{strength:g}>".strip()
    return updated


class BSKCameraControl(scripts.Script):
    def title(self):
        return "BSK Anima Camera Control"

    def show(self, is_img2img):
        return False if is_img2img else scripts.AlwaysVisible

    def ui(self, is_img2img):
        with InputAccordion(value=False, label=self.title(), elem_id=self.elem_id("enable")) as enabled:
            gr.HTML(ORBIT_VIEW, container=False)
            with gr.Row():
                azimuth = gr.Slider(-180, 180, value=0, step=1, label="방향 (°)", info="0° 정면 · ±180° 후면",
                                    elem_id="bsk-camera-azimuth")
                elevation = gr.Slider(-90, 90, value=0, step=1, label="높이 (°)", info="0° 평시점 · 양수는 위에서",
                                      elem_id="bsk-camera-elevation")
            with gr.Row():
                distance = gr.Slider(-100, 100, value=0, step=1, label="거리", info="-100 원경 · +100 극근접",
                                     elem_id="bsk-camera-distance")
                roll = gr.Slider(-180, 180, value=0, step=1, label="기울기 (°)", elem_id="bsk-camera-roll")
            with gr.Row():
                strength = gr.Slider(0, 1, value=DEFAULT_STRENGTH, step=0.05, label="LoRA 강도",
                                     info="제작자 권장 0.6–1.0")
                plain = gr.Checkbox(value=False, label="프롬프트 가중치 생략")
            preview = gr.Textbox(value=camera_tags(0, 0, 0, 0, False), label="카메라 프롬프트", interactive=False)
        controls = (azimuth, elevation, distance, roll, plain)
        for control in controls:
            control.change(camera_tags, inputs=list(controls), outputs=preview, queue=False, show_progress="hidden")
        return [enabled, *controls, strength]

    def process(self, p, enabled, azimuth, elevation, distance, roll, plain, strength):
        if not enabled:
            return
        if not isinstance(p.sd_model, Anima):
            gr.Warning("BSK Anima Camera Control은 Anima 체크포인트에서 작동합니다.")
            return
        if strength > 0 and p.disable_extra_networks:
            gr.Warning("LoRA를 적용하려면 Extra Networks를 활성화해야 합니다.")
            return

        tags = camera_tags(azimuth, elevation, distance, roll, plain)
        p.all_prompts = [apply_camera(prompt, tags, strength) for prompt in p.all_prompts]
        p.main_prompt = p.all_prompts[0]
        if p.all_hr_prompts:
            p.all_hr_prompts = [apply_camera(prompt, tags, strength) for prompt in p.all_hr_prompts]
        p.extra_generation_params["BSK Camera"] = (
            f"azimuth {azimuth:g}, elevation {elevation:g}, distance {distance:g}, roll {roll:g}"
        )
