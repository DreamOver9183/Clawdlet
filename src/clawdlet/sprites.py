"""Sprite sheet loader, nearest-neighbor scaler, and squash/flip transformer."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import sys

from PIL import Image

CHROMA_KEY_RGB = (1, 2, 3)
CHROMA_KEY_HEX = "#010203"


def default_sprites_dir() -> Path:
    """Resolve the sprites directory in both editable installs and PyInstaller single-file bundles."""
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass is not None:
        return Path(str(meipass)) / "assets" / "sprites"
    return Path(__file__).resolve().parents[2] / "assets" / "sprites"


@dataclass(frozen=True)
class SpriteFrame:
    """Single scaled RGBA animation frame."""

    image: Image.Image

    @property
    def width(self) -> int:
        return self.image.width

    @property
    def height(self) -> int:
        return self.image.height

    def render_with_squash(
        self,
        scale_x: float = 1.0,
        scale_y: float = 1.0,
        flip_x: bool = False,
        chroma_rgb: tuple[int, int, int] = CHROMA_KEY_RGB,
    ) -> Image.Image:
        """Return an RGB image composited over ``chroma_rgb`` for Win32 LWA_COLORKEY."""
        src = self.image.transpose(Image.Transpose.FLIP_LEFT_RIGHT) if flip_x else self.image
        w, h = src.size
        canvas = Image.new("RGBA", (w, h), (0, 0, 0, 0))

        if abs(scale_x - 1.0) < 1e-3 and abs(scale_y - 1.0) < 1e-3:
            canvas.paste(src, (0, 0), src)
        else:
            new_w = max(4, round(w * scale_x))
            new_h = max(4, round(h * scale_y))
            deformed = src.resize((new_w, new_h), Image.Resampling.NEAREST)
            # Anchor at bottom-center (feet level) so feet stay flush with the platform
            paste_x = (w - new_w) // 2
            paste_y = h - new_h
            canvas.paste(deformed, (paste_x, paste_y), deformed)

        # Composite onto solid chroma-key background with binary alpha threshold
        bg = Image.new("RGB", (w, h), chroma_rgb)
        alpha = canvas.getchannel("A").point(lambda a: 255 if a >= 128 else 0)
        bg.paste(canvas.convert("RGB"), (0, 0), alpha)
        return bg


@dataclass(frozen=True)
class SpriteAnimation:
    """Frame sequence and playback rate for one animation state."""

    name: str
    frames: tuple[SpriteFrame, ...]
    fps: float
    loop: bool
    loop_start: int = 0

    def frame_at(self, elapsed_s: float) -> SpriteFrame:
        if not self.frames:
            raise RuntimeError(f"Animation {self.name!r} has no frames")
        raw_index = int(max(0.0, elapsed_s) * self.fps)
        if self.loop:
            start = max(0, min(self.loop_start, len(self.frames) - 1))
            if raw_index < start:
                idx = raw_index
            else:
                loop_len = max(1, len(self.frames) - start)
                idx = start + (raw_index - start) % loop_len
        else:
            idx = min(raw_index, len(self.frames) - 1)
        return self.frames[idx]


class SpriteSheetBank:
    """Loads sprite strips from assets/sprites/manifest.json at integer pixel scale."""

    def __init__(self, assets_dir: Path | None = None, scale: int = 2) -> None:
        if scale < 1:
            raise ValueError(f"Scale must be >= 1, got {scale}")
        self.scale = int(scale)

        if assets_dir is None:
            assets_dir = default_sprites_dir()
        self.assets_dir = assets_dir

        manifest_path = self.assets_dir / "manifest.json"
        if not manifest_path.exists():
            from clawdlet.tools.gen_sprites import generate_all_sprites

            generate_all_sprites(self.assets_dir)

        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.base_width = int(manifest.get("frame_width", 64))
        self.base_height = int(manifest.get("frame_height", 64))
        self.scaled_width = self.base_width * self.scale
        self.scaled_height = self.base_height * self.scale

        self._animations: dict[str, SpriteAnimation] = {}
        for name, meta in manifest.get("animations", {}).items():
            strip_path = self.assets_dir / str(meta["file"])
            frame_count = int(meta["frames"])
            fps = float(meta.get("fps", 4.0))
            loop = bool(meta.get("loop", True))
            loop_start = int(meta.get("loop_start", 0))
            self._animations[name] = self._load_strip(
                name=name,
                strip_path=strip_path,
                frame_count=frame_count,
                fps=fps,
                loop=loop,
                loop_start=loop_start,
            )

    def _load_strip(
        self,
        name: str,
        strip_path: Path,
        frame_count: int,
        fps: float,
        loop: bool,
        loop_start: int = 0,
    ) -> SpriteAnimation:
        if not strip_path.exists():
            raise FileNotFoundError(f"Failed to load sprite strip: {strip_path}")

        with Image.open(strip_path) as sheet_raw:
            sheet = sheet_raw.convert("RGBA")
            frames: list[SpriteFrame] = []
            for i in range(frame_count):
                box = (
                    i * self.base_width,
                    0,
                    (i + 1) * self.base_width,
                    self.base_height,
                )
                sub = sheet.crop(box)
                if self.scale != 1:
                    sub = sub.resize(
                        (self.scaled_width, self.scaled_height),
                        Image.Resampling.NEAREST,
                    )
                frames.append(SpriteFrame(image=sub))

        return SpriteAnimation(
            name=name,
            frames=tuple(frames),
            fps=fps,
            loop=loop,
            loop_start=loop_start,
        )

    def get_frame(self, animation_name: str, elapsed_s: float) -> SpriteFrame:
        anim = self._animations.get(animation_name) or self._animations.get("idle_breathe")
        if anim is None:
            raise KeyError(f"No animation found for {animation_name!r}")
        return anim.frame_at(elapsed_s)
