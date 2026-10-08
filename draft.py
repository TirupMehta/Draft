"""Draft v3 — a real Windows app for local speech-to-text.

Golden-hour design, GPU-accurate Pro engine (faster-whisper large-v3-turbo),
instant Flash engine (Whistle 16.9 MB), live partials, voice commands,
compose mode, stats, system tray with autostart, single .exe install.

Hold Right Ctrl, speak, release — text appears where your cursor is.
"""

from __future__ import annotations

import argparse
import array
import ctypes
import json
import logging
import os
import queue
import re
import site
import socket
import sys
import threading
import time
import traceback
import wave

APP_NAME = "Draft"
APP_VERSION = "3.3.0"
SAMPLE_RATE = 16000
MAX_SECONDS = 60
MAX_SAMPLES = SAMPLE_RATE * MAX_SECONDS
_SINGLE_PORT = 47813


def _fix_cuda_path() -> None:
    try:
        if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
            meipass = sys._MEIPASS
            if meipass not in os.environ.get("PATH", ""):
                os.environ["PATH"] = meipass + os.pathsep + os.environ.get("PATH", "")
                try:
                    os.add_dll_directory(meipass)
                except Exception:
                    pass
        for base in site.getsitepackages() + [site.getusersitepackages()]:
            nvidia = os.path.join(base, "nvidia")
            if not os.path.isdir(nvidia):
                continue
            for pkg in os.listdir(nvidia):
                bindir = os.path.join(nvidia, pkg, "bin")
                if os.path.isdir(bindir) and bindir not in os.environ.get("PATH", ""):
                    os.environ["PATH"] = bindir + os.pathsep + os.environ.get("PATH", "")
                    try:
                        os.add_dll_directory(bindir)
                    except Exception:
                        pass
    except Exception:
        pass


_fix_cuda_path()

FROZEN = getattr(sys, "frozen", False)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def resource_path(name: str) -> str:
    base = getattr(sys, "_MEIPASS", BASE_DIR)
    for cand in (os.path.join(base, name),
                 os.path.join(base, "assets", name),
                 os.path.join(BASE_DIR, "assets", name)):
        if os.path.exists(cand):
            return cand
    return name


def data_dir() -> str:
    if FROZEN:
        d = os.path.join(os.environ.get("LOCALAPPDATA",
                                         os.path.expanduser("~")), "Draft")
    else:
        d = BASE_DIR
    os.makedirs(d, exist_ok=True)
    return d


DATA_DIR = data_dir()
CONFIG_PATH = os.path.join(DATA_DIR, "config.json")
HISTORY_PATH = os.path.join(DATA_DIR, "history.json")
STATS_PATH = os.path.join(DATA_DIR, "stats.json")
LOG_PATH = os.path.join(DATA_DIR, "draft.log")

logging.basicConfig(filename=LOG_PATH, level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s",
                    encoding="utf-8")
log = logging.getLogger("draft")


def _excepthook(etype, value, tb):
    log.exception("Uncaught", exc_info=(etype, value, tb))


sys.excepthook = _excepthook
os.environ.setdefault("NEEDLE_TELEMETRY", "0")

# --------------------------------------------------------------------------
# Themes — "Golden Hour" default, "Midnight" alt
# --------------------------------------------------------------------------

THEMES = {
    "golden": {
        "bg": "#100d0b",
        "card": "#1e1714",
        "card2": "#2a2019",
        "border": "#453522",
        "glow": "#6b4f2a",
        "dim": "#8a6f3f",
        "fg": "#f5ede3",
        "muted": "#c0ac90",
        "accent": "#e8bd72",
        "accent_hover": "#c99e52",
        "rose": "#c98a7d",
        "green": "#7ecba1",
        "red": "#e0605e",
        "yellow": "#e8c56a",
    },
    "midnight": {
        "bg": "#0b1020",
        "card": "#141b30",
        "card2": "#1c2440",
        "border": "#2c3a5e",
        "glow": "#3d5a99",
        "dim": "#54678f",
        "fg": "#e8ecf7",
        "muted": "#93a0c0",
        "accent": "#7fb2ff",
        "accent_hover": "#6398e8",
        "rose": "#c99ab8",
        "green": "#7ecba1",
        "red": "#e0605e",
        "yellow": "#e8c56a",
    },
}
THEME_NAMES = tuple(THEMES)


def F_HEAD(size=18):
    return ("Georgia", size, "bold")


def F_BODY(size=12):
    return ("Segoe UI", size)


def F_MONO(size=10):
    return ("Consolas", size)


# --------------------------------------------------------------------------
# Config / stats
# --------------------------------------------------------------------------

DEFAULT_CONFIG = {
    "theme": "golden",
    "engine_mode": "pro",
    "paste_mode": "direct",      # direct | compose
    "hotkey_hold": "right ctrl",
    "hotkey_toggle": "f9",
    "hotkey_dashboard": "ctrl+alt+d",
    "language": "auto",
    "mic": "default",
    "hotwords": "",
    "corrections": [
        ["model registry tirup in", "modelregistry.tirup.in"],
        ["modelregistry tirup in", "modelregistry.tirup.in"],
        ["tirup mehta", "Tirup Mehta"],
        ["tiroop mehta", "Tirup Mehta"],
        ["tyrup mehta", "Tirup Mehta"],
        ["tirukmata", "Tirup Mehta"],
        ["tirup meta", "Tirup Mehta"],
        ["tirup in", "tirup.in"],
        ["tiroop", "Tirup"],
        ["tyrup", "Tirup"],
    ],
    "remove_fillers": True,
    "smart_dots": True,
    "auto_paste": True,
    "copy_to_clipboard": True,
    "trailing_space": True,
    "clean_output": True,
    "voice_commands": True,
    "live_partials": True,
    "sounds": True,
    "show_overlay": True,
    "autostart": True,
    "start_minimized": False,
    "welcomed": False,
}

LANGUAGES = ("auto", "en", "de", "fr", "es", "it", "nl", "pl")
ENGINE_MODES = ("pro", "flash")
PASTE_MODES = ("direct", "compose")


def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    try:
        if os.path.exists(CONFIG_PATH):
            with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
                user = json.load(fh)
            for key in DEFAULT_CONFIG:
                if key in user:
                    cfg[key] = user[key]
    except Exception:
        pass
    if cfg.get("language") not in LANGUAGES:
        cfg["language"] = "auto"
    if cfg.get("engine_mode") not in ENGINE_MODES:
        cfg["engine_mode"] = "pro"
    if cfg.get("paste_mode") not in PASTE_MODES:
        cfg["paste_mode"] = "direct"
    if cfg.get("theme") not in THEMES:
        cfg["theme"] = "golden"
    if not isinstance(cfg.get("corrections"), list):
        cfg["corrections"] = list(DEFAULT_CONFIG["corrections"])
    if "keywords" in cfg and not cfg.get("hotwords"):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
                old = json.load(fh)
            if isinstance(old.get("keywords"), str):
                cfg["hotwords"] = old["keywords"]
        except Exception:
            pass
    return cfg


def save_config(cfg: dict) -> None:
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh, indent=2)
    except Exception as exc:
        log.warning("save_config: %s", exc)


def load_stats() -> dict:
    s = {"dictations": 0, "words": 0, "chars": 0}
    try:
        if os.path.exists(STATS_PATH):
            with open(STATS_PATH, "r", encoding="utf-8") as fh:
                s.update(json.load(fh))
    except Exception:
        pass
    return s


def save_stats(s: dict) -> None:
    try:
        with open(STATS_PATH, "w", encoding="utf-8") as fh:
            json.dump(s, fh, indent=1)
    except Exception as exc:
        log.warning("save_stats: %s", exc)


def parse_hotwords(raw: str) -> str | None:
    if not raw:
        return None
    if isinstance(raw, (list, tuple)):
        raw = ", ".join(str(k) for k in raw)
    text = " ".join(str(raw).replace(",", " ").split())
    return text or None


def hotword_list(raw: str) -> list[str]:
    return [k for k in str(raw or "").replace(",", " ").split() if k]


def lang_or_none(cfg_lang: str, pro_engine: bool = False):
    if not cfg_lang or cfg_lang == "auto":
        return None
    if pro_engine:
        return cfg_lang
    return cfg_lang if cfg_lang in ("en", "de", "fr", "es", "it", "nl", "pl") else None


def fmt_uptime(sec: float) -> str:
    sec = int(sec)
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


# --------------------------------------------------------------------------
# Autostart (frozen app only — writes HKCU Run key)
# --------------------------------------------------------------------------

def autostart_enabled() -> bool:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Run") as k:
            winreg.QueryValueEx(k, APP_NAME)
        return True
    except Exception:
        return False


def set_autostart(enable: bool) -> bool:
    if not FROZEN:
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Run",
                            0, winreg.KEY_SET_VALUE) as k:
            if enable:
                winreg.SetValueEx(k, APP_NAME, 0, winreg.REG_SZ,
                                  f'"{sys.executable}" --minimized')
            else:
                try:
                    winreg.DeleteValue(k, APP_NAME)
                except FileNotFoundError:
                    pass
        return True
    except Exception as exc:
        log.warning("autostart: %s", exc)
        return False


# --------------------------------------------------------------------------
# Audio preprocessing (pure numpy)
# --------------------------------------------------------------------------

def _np():
    import numpy as np
    return np


def remove_dc(audio):
    np = _np()
    x = np.asarray(audio, dtype=np.float32)
    if x.size == 0:
        return x
    return (x - float(np.mean(x))).astype(np.float32)


def auto_gain(audio, target: float = 0.9, max_boost: float = 8.0):
    np = _np()
    x = np.asarray(audio, dtype=np.float32)
    if x.size == 0:
        return x
    peak = float(np.max(np.abs(x)))
    if peak < 1e-4:
        return x
    if peak < 0.30:
        gain = min(target / peak, max_boost)
        x = x * gain
    elif peak > 0.98:
        x = x * (0.98 / peak)
    np.clip(x, -1.0, 1.0, out=x)
    return x.astype(np.float32)


def trim_silence(audio, sr: int = SAMPLE_RATE, thresh_db: float = -42.0,
                 frame_ms: int = 20, pad_ms: int = 150):
    np = _np()
    x = np.asarray(audio, dtype=np.float32)
    if x.size < sr // 5:
        return x, True
    frame = max(1, int(sr * frame_ms / 1000))
    pad = int(sr * pad_ms / 1000)
    n_frames = int(len(x) // frame)
    if n_frames < 2:
        return x, True
    frames = x[:n_frames * frame].reshape(n_frames, frame)
    rms = np.sqrt(np.mean(frames ** 2, axis=1) + 1e-12)
    thresh = 10.0 ** (thresh_db / 20.0)
    voiced = rms > thresh
    if not bool(np.any(voiced)):
        return x, False
    idx = np.where(voiced)[0]
    start = max(0, int(idx[0]) * frame - pad)
    end = min(len(x), int(idx[-1] + 1) * frame + pad)
    return x[start:end].astype(np.float32), True


def preprocess(audio, sr: int = SAMPLE_RATE):
    np = _np()
    x = np.asarray(audio, dtype=np.float32).ravel()
    if x.size == 0:
        return x, False
    if x.size > MAX_SAMPLES:
        x = x[:MAX_SAMPLES]
    x = remove_dc(x)
    x = auto_gain(x)
    x, had_speech = trim_silence(x, sr=sr)
    return x.astype(np.float32), had_speech


def resample_to_16k(audio, from_rate: int):
    np = _np()
    x = np.asarray(audio, dtype=np.float32).ravel()
    if from_rate == SAMPLE_RATE or x.size == 0:
        return x.astype(np.float32)
    try:
        import soxr
        return soxr.resample(x, from_rate, SAMPLE_RATE, quality="HQ").astype(np.float32)
    except ImportError:
        ratio = SAMPLE_RATE / float(from_rate)
        old_idx = np.arange(x.size)
        new_len = max(1, int(x.size * ratio))
        new_idx = np.linspace(0, x.size - 1, new_len)
        return np.interp(new_idx, old_idx, x).astype(np.float32)


# --------------------------------------------------------------------------
# Text cleanup + voice commands
# --------------------------------------------------------------------------

_SPACE_BEFORE_PUNCT = re.compile(r"\s+([,.!?;:%)\]}])")
_OPEN_SPACE_AFTER = re.compile(r"([(\[{])\s+")
_MULTI_SPACE = re.compile(r"\s{2,}")
_SENT_END = re.compile(r"([.!?…])\s+([a-zäöüßàâçéèêëîïôûù])")
_MISSING_SPACE_AFTER = re.compile(r"([,;!?])(?=[A-Za-zÀ-ÿ0-9])")
_MISSING_SPACE_PERIOD = re.compile(r"(?<=[a-zäöüßàâçéèêëîïôûù])\.(?=[A-ZÄÖÜÀÂÇÉÈÊËÎÏÔÛÙ])")
_MISSING_SPACE_COLON = re.compile(r":(?=[A-Za-zÀ-ÿ])")


def _cap_match(match: "re.Match") -> str:
    return match.group(1) + " " + match.group(2).upper()


def clean_text(text: str, trailing_space: bool = True) -> str:
    t = (text or "").strip()
    if not t:
        return ""
    t = _MULTI_SPACE.sub(" ", t.replace("\n", " "))
    t = _SPACE_BEFORE_PUNCT.sub(r"\1", t)
    t = _MISSING_SPACE_AFTER.sub(r"\1 ", t)
    t = _MISSING_SPACE_PERIOD.sub(". ", t)
    t = _MISSING_SPACE_COLON.sub(": ", t)
    t = _MULTI_SPACE.sub(" ", t)
    t = _OPEN_SPACE_AFTER.sub(r"\1", t)
    t = _SENT_END.sub(_cap_match, t)
    if t and t[0].islower():
        t = t[0].upper() + t[1:]
    if trailing_space and not t.endswith((" ", "\n")):
        t += " "
    return t


_COMMAND_PATTERNS = (
    (re.compile(r"^\s*scratch that\s*[.!]?\s*$", re.I), "scratch"),
    (re.compile(r"^\s*new paragraph\s*[.!]?\s*$", re.I), "paragraph"),
    (re.compile(r"^\s*new line\s*[.!]?\s*$", re.I), "newline"),
)
_INLINE_COMMANDS = (
    (re.compile(r"\bnew paragraph\b[,.!]?", re.I), "\n\n"),
    (re.compile(r"\bnew line\b[,.!]?", re.I), "\n"),
)


def apply_voice_commands(text: str):
    stripped = (text or "").strip()
    for pattern, kind in _COMMAND_PATTERNS:
        if pattern.match(stripped):
            if kind == "scratch":
                return "scratch", ""
            if kind == "paragraph":
                return "paste", "\n\n"
            if kind == "newline":
                return "paste", "\n"
    out = stripped
    for pattern, repl in _INLINE_COMMANDS:
        out = pattern.sub(repl, out)
    return "text", out


def _dot_join_match(match: "re.Match") -> str:
    parts = re.split(r"\s+dot\s+", match.group(0), flags=re.I)
    return ".".join(parts)


def apply_corrections(text: str, pairs, smart_dots: bool = True) -> str:
    """Taught words: guaranteed replacements, longest match first.

    pairs is [[heard, written], ...]. Runs before cleanup so spacing and
    casing normalize afterwards.
    """
    out = text or ""
    clean_pairs = [(str(w).strip(), str(r)) for w, r in (pairs or [])
                   if str(w).strip() and str(r)]
    for wrong, right in sorted(clean_pairs, key=lambda p: -len(p[0])):
        try:
            pat = re.compile(r"(?<![\w.])" + re.escape(wrong) + r"(?![\w.])",
                             re.IGNORECASE)
            out = pat.sub(right, out)
        except Exception:
            pass
    if smart_dots:
        # "tirup dot in" -> "tirup.in", "a dot b dot c" -> "a.b.c"
        out = re.sub(r"\b[\w-]+(?:\s+dot\s+[\w-]+)+", _dot_join_match, out,
                     flags=re.I)
    return out


_FILLER_PAT = re.compile(r"(?i)\b(um+|uh+|hmm+|hm+|er+|ah+)(?=[,.]|\s|$)")


def remove_fillers(text: str) -> str:
    """Drop hesitation sounds (um, uh, hmm) without touching real words."""
    return _MULTI_SPACE.sub(" ", _FILLER_PAT.sub("", text or "")).strip()


# --------------------------------------------------------------------------
# Engines — Pro (turbo/CUDA) default, Flash (Whistle/CPU) fallback
# --------------------------------------------------------------------------

class ProEngine:
    MODEL_ID = "large-v3-turbo"

    def __init__(self):
        self._model = None
        self._lock = threading.Lock()
        self.device = "cpu"
        self.ready = False
        self.error: str | None = None
        self.model_id = self.MODEL_ID

    def load(self) -> bool:
        _fix_cuda_path()
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            self.error = f"faster-whisper not installed ({exc})"
            return False
        for device, compute in (("cuda", "float16"), ("cpu", "int8")):
            try:
                model = WhisperModel(self.MODEL_ID, device=device,
                                     compute_type=compute)
                with self._lock:
                    self._model = model
                    self.device = device
                    self.ready = True
                log.info("Pro engine on %s", device)
                return True
            except Exception as exc:  # noqa: BLE001
                last = exc
                continue
        self.error = f"Could not load {self.MODEL_ID}: {last}"
        log.error(self.error)
        return False

    def _decode(self, samples, language, hotwords, beam_size, vad,
                prompt=None):
        import numpy as np
        with self._lock:
            model = self._model
        x = np.asarray(samples, dtype=np.float32).ravel()
        kwargs = dict(language=language, beam_size=beam_size,
                      vad_filter=vad, condition_on_previous_text=False,
                      temperature=0.0)
        if hotwords:
            kwargs["hotwords"] = hotwords
        if prompt:
            kwargs["initial_prompt"] = prompt
        segments, info = model.transcribe(x, **kwargs)
        text = " ".join(s.text.strip() for s in segments).strip()
        lang = getattr(info, "language", "") or ""
        prob = float(getattr(info, "language_probability", 0.0) or 0.0)
        return text, lang, prob

    def transcribe(self, audio_16k, language=None, hotwords=None,
                     prompt=None) -> dict:
        import numpy as np
        if self._model is None:
            raise RuntimeError("Pro engine is still loading.")
        samples = np.asarray(audio_16k, dtype=np.float32).ravel()
        if samples.size == 0:
            return {"text": "", "language": "", "total_ms": 0.0,
                    "rtf": 0.0, "truncated": False}
        truncated = bool(samples.size > MAX_SAMPLES)
        if truncated:
            samples = samples[:MAX_SAMPLES]
        started = time.perf_counter()
        text, lang, _prob = self._decode(samples, language, hotwords,
                                         beam_size=5, vad=True, prompt=prompt)
        total_ms = (time.perf_counter() - started) * 1000.0
        dur = max(0.1, samples.size / SAMPLE_RATE)
        return {"text": text, "language": lang, "total_ms": total_ms,
                "rtf": dur / max(total_ms / 1000.0, 1e-6),
                "truncated": truncated}

    def partial(self, audio_16k, language=None, prompt=None) -> str:
        try:
            import numpy as np
            x = np.asarray(audio_16k, dtype=np.float32).ravel()
            if x.size < SAMPLE_RATE // 2:
                return ""
            if x.size > SAMPLE_RATE * 20:
                x = x[-SAMPLE_RATE * 20:]
            text, _lang, _p = self._decode(x, language, None,
                                           beam_size=1, vad=False,
                                           prompt=prompt)
            return text
        except Exception:
            return ""


class FlashEngine:
    def __init__(self):
        self._model = None
        self._lock = threading.Lock()
        self.ready = False
        self.error: str | None = None
        self.weights_mb = 0.0

    def load(self) -> bool:
        try:
            import needle
            model = needle.Whistle()
            try:
                import numpy as np
                model.transcribe(np.zeros(SAMPLE_RATE // 2, dtype=np.float32))
            except Exception:
                pass
            with self._lock:
                self._model = model
                try:
                    self.weights_mb = os.path.getsize(model.weights) / 1e6
                except Exception:
                    pass
                self.ready = True
            log.info("Flash engine ready")
            return True
        except Exception as exc:  # noqa: BLE001
            self.error = f"{exc}"
            log.error("Flash: %s", exc)
            return False

    def transcribe(self, audio_16k, language=None, hotwords=None) -> dict:
        import numpy as np
        with self._lock:
            model = self._model
        if model is None:
            raise RuntimeError("Flash engine is still loading.")
        samples = np.asarray(audio_16k, dtype=np.float32).ravel()
        if samples.size == 0:
            return {"text": "", "language": "", "total_ms": 0.0,
                    "rtf": 0.0, "truncated": False}
        truncated = bool(samples.size > 480000)
        if truncated:
            samples = samples[:480000]
        kws = [k.strip() for k in str(hotwords or "").replace(",", " ").split()
               if k.strip()] or None
        started = time.perf_counter()
        result = model.transcribe(samples, language=language, keywords=kws)
        total_ms = (time.perf_counter() - started) * 1000.0
        dur = max(0.1, samples.size / SAMPLE_RATE)
        return {"text": result.get("text", ""),
                "language": result.get("language", ""),
                "total_ms": total_ms,
                "rtf": dur / max(total_ms / 1000.0, 1e-6),
                "ttft_ms": float(result.get("ttft_ms", 0.0) or 0.0),
                "decode_tps": float(result.get("decode_tps", 0.0) or 0.0),
                "truncated": truncated}

    def partial(self, audio_16k, language=None) -> str:
        try:
            import numpy as np
            x = np.asarray(audio_16k, dtype=np.float32).ravel()
            if x.size < SAMPLE_RATE // 2:
                return ""
            with self._lock:
                model = self._model
            tail = x[-480000:] if x.size > 480000 else x
            return (model.transcribe(tail, language=language).get("text") or "").strip()
        except Exception:
            return ""


class EngineManager:
    def __init__(self, mode: str = "pro"):
        self.mode = mode if mode in ENGINE_MODES else "pro"
        self.pro = ProEngine()
        self.flash = FlashEngine()
        self.error: str | None = None
        self._reloading = False

    @property
    def active(self):
        return self.pro if self.mode == "pro" else self.flash

    @property
    def ready(self) -> bool:
        if self.active.ready:
            return True
        return self.flash.ready

    def describe(self) -> str:
        if self.mode == "pro" and self.pro.ready:
            return "turbo-GPU" if self.pro.device == "cuda" else "turbo-CPU"
        if self.flash.ready:
            return f"whistle {self.flash.weights_mb:.1f} MB"
        return "loading…"

    def load(self, status=None) -> bool:
        def _say(msg):
            try:
                if status:
                    status(msg)
            except Exception:
                pass
        _say("Loading Pro engine (large-v3-turbo)…")
        pro_ok = self.pro.load()
        if pro_ok:
            _say(f"Pro engine ready on {self.pro.device}.")
        else:
            _say(f"Pro engine unavailable ({self.pro.error}).")
        _say("Loading Flash engine (Whistle 16.9 MB)…")
        flash_ok = self.flash.load()
        if self.mode == "pro" and not pro_ok and flash_ok:
            self.mode = "flash"
            _say("Pro unavailable — auto-switched to Flash mode.")
        if not pro_ok and not flash_ok:
            self.error = f"{self.pro.error} | {self.flash.error}"
            return False
        return True

    def reload(self, status=None) -> bool:
        if self._reloading:
            return False
        self._reloading = True
        try:
            self.pro.ready = False
            self.flash.ready = False
            return self.load(status=status)
        finally:
            self._reloading = False

    @staticmethod
    def guidance(hotwords, corrections) -> str | None:
        """One bias string from hotwords + taught written-forms.

        Fed to faster-whisper as hotwords + initial_prompt, and to
        Whistle as keyword bias — so taught words win at decode time.
        """
        seen: list[str] = []
        for w in str(hotwords or "").replace(",", " ").split():
            if w not in seen:
                seen.append(w)
        for _w, r in (corrections or []):
            for tok in str(r).split():
                if tok not in seen:
                    seen.append(tok)
        return " ".join(seen) or None

    def transcribe(self, audio_16k, language=None, hotwords=None,
                   corrections=None) -> dict:
        engine = self.active if self.active.ready else self.flash
        if not engine.ready:
            raise RuntimeError("Speech engines are still loading — one moment.")
        guide = self.guidance(hotwords, corrections)
        if engine is self.pro:
            return engine.transcribe(audio_16k, language=language,
                                     hotwords=hotwords, prompt=guide)
        return engine.transcribe(audio_16k, language=language,
                                 hotwords=guide)

    def partial(self, audio_16k, language=None, hotwords=None,
                corrections=None) -> str:
        engine = self.active if self.active.ready else self.flash
        if not engine.ready:
            return ""
        guide = self.guidance(hotwords, corrections)
        try:
            if engine is self.pro:
                return engine.partial(audio_16k, language=language,
                                      prompt=guide)
            return engine.partial(audio_16k, language=language)
        except Exception:
            return ""


# --------------------------------------------------------------------------
# Microphone recorder
# --------------------------------------------------------------------------

class MicRecorder:
    def __init__(self):
        self._stream = None
        self._chunks: list = []
        self._peaks: list = []
        self._rate = SAMPLE_RATE
        self._level = 0.0
        self._recording = False
        self._lock = threading.Lock()

    @property
    def level(self) -> float:
        with self._lock:
            return self._level

    @property
    def recording(self) -> bool:
        with self._lock:
            return self._recording

    def list_inputs(self) -> list[dict]:
        try:
            import sounddevice as sd
            return [d for d in sd.query_devices() if d.get("max_input_channels", 0) > 0]
        except Exception:
            return []

    def resolve_device(self, cfg_mic) -> tuple:
        default_rate = SAMPLE_RATE
        try:
            import sounddevice as sd
            if isinstance(cfg_mic, int):
                info = sd.query_devices(cfg_mic)
                return cfg_mic, int(info.get("default_samplerate") or SAMPLE_RATE)
            if isinstance(cfg_mic, str) and cfg_mic not in ("default", "", "auto"):
                if cfg_mic.isdigit():
                    idx = int(cfg_mic)
                    info = sd.query_devices(idx)
                    return idx, int(info.get("default_samplerate") or SAMPLE_RATE)
                for i, dev in enumerate(sd.query_devices()):
                    if dev.get("max_input_channels", 0) > 0 and \
                            cfg_mic.lower() in str(dev.get("name", "")).lower():
                        return i, int(dev.get("default_samplerate") or SAMPLE_RATE)
            info = sd.query_devices(kind="input")
            default_rate = int(info.get("default_samplerate") or SAMPLE_RATE)
            return None, default_rate
        except Exception:
            return None, default_rate

    def _callback(self, indata, frames, time_info, status):
        try:
            import numpy as np
            data = np.asarray(indata[:, 0], dtype=np.float32).copy()
            with self._lock:
                self._chunks.append(data)
                rms = float(np.sqrt(np.mean(data ** 2) + 1e-12))
                self._level = 0.7 * self._level + 0.3 * min(1.0, rms * 4.0)
                peak = float(np.max(np.abs(data))) if data.size else 0.0
                self._peaks.append(min(1.0, peak * 2.5))
                if len(self._peaks) > 140:
                    del self._peaks[:-140]
        except Exception:
            pass

    def start(self, cfg_mic="default") -> int:
        import sounddevice as sd
        self.stop()
        device, rate = self.resolve_device(cfg_mic)
        with self._lock:
            self._chunks = []
            self._peaks = []
            self._level = 0.0
            self._recording = True
        tried = []
        for want in (SAMPLE_RATE, rate):
            if want in tried:
                continue
            tried.append(want)
            try:
                stream = sd.InputStream(samplerate=want, channels=1,
                                        dtype="float32", device=device,
                                        latency="low", callback=self._callback)
                stream.start()
                self._stream = stream
                self._rate = want
                return want
            except Exception:
                continue
        stream = sd.InputStream(channels=1, dtype="float32", device=device,
                                callback=self._callback)
        stream.start()
        self._stream = stream
        try:
            self._rate = int(stream.samplerate)
        except Exception:
            self._rate = rate
        return self._rate

    def snapshot(self):
        import numpy as np
        with self._lock:
            chunks = list(self._chunks)
            rate = self._rate
        if not chunks:
            return np.zeros(0, dtype=np.float32), SAMPLE_RATE
        audio = np.concatenate(chunks).astype(np.float32)
        if rate != SAMPLE_RATE:
            audio = resample_to_16k(audio, rate)
        return audio, SAMPLE_RATE

    def peaks_snapshot(self) -> list:
        """Recent mic peaks for the live waveform. Thread-safe."""
        with self._lock:
            return list(self._peaks)

    def stop(self):
        stream, chunks, rate = None, [], self._rate
        with self._lock:
            stream, self._stream = self._stream, None
            chunks, self._chunks = self._chunks, []
            self._recording = False
            self._level = 0.0
        if stream is not None:
            try:
                stream.stop()
            except Exception:
                pass
            try:
                stream.close()
            except Exception:
                pass
        if not chunks:
            import numpy as np
            return np.zeros(0, dtype=np.float32), rate
        import numpy as np
        audio = np.concatenate(chunks).astype(np.float32)
        if rate != SAMPLE_RATE:
            audio = resample_to_16k(audio, rate)
        return audio, SAMPLE_RATE

    def abort(self):
        self.stop()


# --------------------------------------------------------------------------
# Paste / sound helpers
# --------------------------------------------------------------------------

def beep(freq: int, ms: int = 60):
    try:
        import winsound
        winsound.Beep(freq, ms)
    except Exception:
        pass


def paste_text(text: str, restore_clipboard: bool = True) -> bool:
    if not text:
        return False
    try:
        import pyperclip
    except ImportError:
        return _type_fallback(text)
    try:
        original = None
        if restore_clipboard:
            try:
                original = pyperclip.paste()
            except Exception:
                original = None
        pyperclip.copy(text)
        time.sleep(0.03)
        if not _send_paste_key():
            return _type_fallback(text)
        if restore_clipboard and original is not None:
            def _restore():
                time.sleep(0.8)
                try:
                    if pyperclip.paste() == text:
                        pyperclip.copy(original)
                except Exception:
                    pass
            threading.Thread(target=_restore, daemon=True).start()
        return True
    except Exception:
        traceback.print_exc()
        return _type_fallback(text)


def _send_paste_key() -> bool:
    try:
        import keyboard
        keyboard.send("ctrl+v")
        return True
    except Exception:
        return False


def _type_fallback(text: str) -> bool:
    try:
        import keyboard
        keyboard.write(text)
        return True
    except Exception:
        return False


# --------------------------------------------------------------------------
# Global hotkeys
# --------------------------------------------------------------------------

# Side-precise key groups. The `keyboard` lib matches hotkeys by scan code
# and its table bleeds left-ctrl into right-ctrl, so we match on the
# side-specific event names Windows actually reports instead.
_GENERIC_SIDES = {
    "ctrl": {"left ctrl", "right ctrl"},
    "control": {"left ctrl", "right ctrl"},
    "shift": {"left shift", "right shift"},
    "alt": {"left alt", "right alt"},
    "menu": {"left alt", "right alt"},
    "win": {"left windows", "right windows"},
    "windows": {"left windows", "right windows"},
    "super": {"left windows", "right windows"},
    "meta": {"left windows", "right windows"},
}


def _names_for(key: str) -> set:
    k = (key or "").strip().lower()
    if k in _GENERIC_SIDES:
        return set(_GENERIC_SIDES[k])
    try:
        from keyboard._canonical_names import canonical_names as _cn
        k = _cn.get(k, k)
    except Exception:
        pass
    return {k} if k else set()


def _parse_key(key: str) -> dict:
    """Single key -> {'combo': None, 'finals': {names}};
    chord like ctrl+alt+d -> {'combo': full, 'finals': {final names}}."""
    parts = [p.strip().lower() for p in str(key or "").split("+") if p.strip()]
    if len(parts) > 1:
        finals: set = set()
        for n in _names_for(parts[-1]):
            finals.add(n)
        return {"combo": "+".join(parts), "finals": finals}
    return {"combo": None, "finals": _names_for(parts[0] if parts else "")}


class HotkeyManager:
    def __init__(self, on_hold_start, on_hold_stop, on_toggle, on_dashboard):
        self.on_hold_start = on_hold_start
        self.on_hold_stop = on_hold_stop
        self.on_toggle = on_toggle
        self.on_dashboard = on_dashboard
        self.hold_key = "right ctrl"
        self.toggle_key = "f9"
        self.dashboard_key = "ctrl+alt+d"
        self.available = False
        self._holding = False
        self._down: set = set()
        self._specs: dict = {}

    def start(self, hold_key: str, toggle_key: str, dashboard_key: str = ""):
        self.hold_key = (hold_key or "right ctrl").lower()
        self.toggle_key = (toggle_key or "f9").lower()
        self.dashboard_key = (dashboard_key or "ctrl+alt+d").lower()
        try:
            import keyboard
        except ImportError:
            return False
        try:
            keyboard.unhook_all()
        except Exception:
            pass
        try:
            self._specs = {
                "hold": _parse_key(self.hold_key),
                "toggle": _parse_key(self.toggle_key),
                "dash": _parse_key(self.dashboard_key),
            }
            # Same physical key for two roles: hold wins, others stand down.
            if self._specs["toggle"] == self._specs["hold"]:
                self._specs["toggle"] = {"combo": None, "finals": set()}
            if self._specs["dash"] == self._specs["hold"]:
                self._specs["dash"] = {"combo": None, "finals": set()}
            self._down = set()
            self._holding = False
            keyboard.hook(self._raw, suppress=False)
            self.available = True
            return True
        except Exception:
            log.exception("hotkeys")
            self.available = False
            return False

    def _fire(self, role: str, is_down: bool, repeat: bool) -> bool:
        if role == "hold":
            if is_down:
                self._hold_down()
            else:
                self._hold_up()
            return True
        if repeat:
            return True  # ignore auto-repeat for toggles
        if role == "toggle" and is_down:
            self._toggle_down()
        elif role == "dash" and is_down:
            self._dashboard_down()
        return True

    def _match(self, name: str, is_down: bool, repeat: bool) -> None:
        try:
            import keyboard
        except ImportError:
            return
        for role in ("hold", "toggle", "dash"):
            spec = self._specs.get(role, {})
            if not spec.get("finals") or name not in spec["finals"]:
                continue
            if spec.get("combo"):
                if not is_down or repeat:
                    if not is_down and role == "hold":
                        self._hold_up()
                    return
                try:
                    if keyboard.is_pressed(spec["combo"]):
                        self._fire(role, True, False)
                except Exception:
                    pass
                return
            self._fire(role, is_down, repeat)
            return

    def _raw(self, event) -> None:
        try:
            name = (getattr(event, "name", "") or "").lower()
            if not name:
                return
            if event.event_type == "down":
                repeat = name in self._down
                self._down.add(name)
                self._match(name, True, repeat)
            elif event.event_type == "up":
                self._down.discard(name)
                self._match(name, False, False)
        except Exception:
            traceback.print_exc()

    def _hold_down(self, _event=None):
        if self._holding:
            return
        self._holding = True
        try:
            self.on_hold_start()
        except Exception:
            traceback.print_exc()

    def _hold_up(self, _event=None):
        if not self._holding:
            return
        self._holding = False
        try:
            self.on_hold_stop()
        except Exception:
            traceback.print_exc()

    def _toggle_down(self, _event=None):
        try:
            self.on_toggle()
        except Exception:
            traceback.print_exc()

    def _dashboard_down(self, _event=None):
        try:
            self.on_dashboard()
        except Exception:
            traceback.print_exc()


# --------------------------------------------------------------------------
# UI — golden-hour premium
# --------------------------------------------------------------------------

def _hex(h: str) -> tuple:
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _shade(rgb: tuple, f: float) -> tuple:
    if f >= 1.0:
        return tuple(min(255, int(c + (255 - c) * (f - 1.0))) for c in rgb)
    return tuple(max(0, int(c * f)) for c in rgb)


class MicArt:
    """Antialiased hero mic, rendered with Pillow: gradient disc, soft
    shadow, espresso glyph, expanding rings while recording."""

    SIZE = 208
    R = 74

    def __init__(self, T: dict):
        self.T = T
        self._idle_img = None
        self._idle_photo = None

    def _disc(self, accent_hex: str):
        from PIL import Image, ImageDraw, ImageFilter
        S, R = self.SIZE, self.R
        cx = cy = S // 2
        base = _hex(accent_hex)
        light = _shade(base, 1.35)
        dark = _shade(base, 0.62)
        img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        # soft drop shadow
        sh = Image.new("RGBA", (S, S), (0, 0, 0, 0))
        ImageDraw.Draw(sh).ellipse([cx - R, cy - R + 7, cx + R, cy + R + 7],
                                   fill=(0, 0, 0, 110))
        sh = sh.filter(ImageFilter.GaussianBlur(7))
        img = Image.alpha_composite(img, sh)
        d = ImageDraw.Draw(img)
        # vertical gradient disc, masked to circle
        grad = Image.new("RGBA", (2 * R + 2, 2 * R + 2))
        gd = ImageDraw.Draw(grad)
        for y in range(2 * R + 2):
            f = y / max(1, 2 * R + 1)
            if f < 0.5:
                k = f * 2
                col = tuple(int(light[i] + (base[i] - light[i]) * k)
                            for i in range(3))
            else:
                k = (f - 0.5) * 2
                col = tuple(int(base[i] + (dark[i] - base[i]) * k)
                            for i in range(3))
            gd.line([(0, y), (2 * R + 1, y)], fill=col + (255,))
        mask = Image.new("L", (2 * R + 2, 2 * R + 2), 0)
        ImageDraw.Draw(mask).ellipse([0, 0, 2 * R + 1, 2 * R + 1], fill=255)
        img.paste(grad, (cx - R - 1, cy - R - 1), mask)
        d = ImageDraw.Draw(img)
        rim = _shade(base, 0.55)
        d.ellipse([cx - R, cy - R, cx + R, cy + R], outline=rim + (255,),
                  width=3)
        return img, d, (cx, cy)

    @staticmethod
    def _glyph(d, cx: int, cy: int):
        ink = (36, 26, 16, 255)
        ivory = (245, 237, 227, 255)
        d.rounded_rectangle([cx - 16, cy - 36, cx + 16, cy + 4], radius=16,
                            fill=ink)
        d.ellipse([cx + 3, cy - 26, cx + 11, cy - 18], fill=ivory)
        d.line([cx - 13, cy + 2, cx - 5, cy + 24], fill=ink, width=5)
        d.line([cx + 13, cy + 2, cx + 5, cy + 24], fill=ink, width=5)
        d.line([cx, cy + 22, cx, cy + 34], fill=ink, width=5)
        d.line([cx - 14, cy + 34, cx + 14, cy + 34], fill=ink, width=5)

    def idle(self):
        if self._idle_img is None:
            img, d, (cx, cy) = self._disc(self.T["accent"])
            self._glyph(d, cx, cy)
            self._idle_img = img
        return self._idle_img

    def idle_photo(self):
        from PIL import ImageTk
        if self._idle_photo is None:
            self._idle_photo = ImageTk.PhotoImage(self.idle())
        return self._idle_photo

    def frame(self, phase: float):
        img, d, (cx, cy) = self._disc(self.T["yellow"])
        for k in range(2):
            rr = self.R + 10 + ((phase * 16 + k * 22) % 44)
            alpha = max(0, 150 - int((rr - self.R) * 3.4))
            ring = _hex(self.T["glow"]) + (alpha,)
            d.ellipse([cx - rr, cy - rr, cx + rr, cy + rr], outline=ring,
                      width=3)
        self._glyph(d, cx, cy)
        return img


class Overlay:
    """Borderless glowing pill with fade in/out and live partials."""

    def __init__(self, root, T: dict):
        self.root = root
        self.T = T
        self.win = None
        self._status = None
        self._partial = None
        self._bar = None
        self._hide_after = None
        self._fade_after = None

    def _ensure(self):
        import customtkinter as ctk
        T = self.T
        if self.win is not None and self.win.winfo_exists():
            return
        win = ctk.CTkToplevel(self.root)
        win.overrideredirect(True)
        try:
            win.attributes("-topmost", True)
        except Exception:
            pass
        try:
            win.attributes("-alpha", 0.0)
        except Exception:
            pass
        win.configure(fg_color=T["bg"])
        # outer glow frame
        glow = ctk.CTkFrame(win, fg_color=T["glow"], corner_radius=18)
        glow.pack(padx=0, pady=0)
        frame = ctk.CTkFrame(glow, fg_color="#171210", corner_radius=16,
                             border_width=1, border_color=T["border"])
        frame.pack(padx=1, pady=1)
        self._status = ctk.CTkLabel(frame, text="● REC  0.0s",
                                    text_color=T["red"],
                                    font=("Segoe UI", 13, "bold"))
        self._status.pack(anchor="w", padx=18, pady=(12, 2))
        self._partial = ctk.CTkLabel(frame, text="", text_color=T["muted"],
                                     font=("Segoe UI", 12),
                                     wraplength=360, justify="left")
        self._partial.pack(anchor="w", padx=18, pady=(0, 2))
        self._sub = ctk.CTkLabel(frame, text="", text_color="#8a7a63",
                                 font=("Segoe UI", 10, "italic"))
        self._sub.pack(anchor="w", padx=18, pady=(0, 4))
        self._bar = ctk.CTkProgressBar(frame, width=340, height=6,
                                       progress_color=T["accent"],
                                       fg_color="#2c2318")
        self._bar.pack(padx=18, pady=(0, 14))
        self._bar.set(0.0)
        self.win = win
        self.win.withdraw()

    def _place_top_center(self):
        self.win.update_idletasks()
        sw = self.win.winfo_screenwidth()
        w = self.win.winfo_reqwidth()
        self.win.geometry(f"+{(sw - w) // 2}+26")

    def _fade(self, target: float, done=None):
        if self._fade_after:
            try:
                self.root.after_cancel(self._fade_after)
            except Exception:
                pass
            self._fade_after = None
        try:
            cur = float(self.win.attributes("-alpha"))
        except Exception:
            cur = target
        step = 0.12 if target > cur else -0.15
        nxt = cur + step
        finished = (nxt >= target) if step > 0 else (nxt <= target)
        try:
            self.win.attributes("-alpha", target if finished else nxt)
        except Exception:
            finished = True
        if finished:
            if done:
                try:
                    done()
                except Exception:
                    pass
        else:
            self._fade_after = self.root.after(15, lambda: self._fade(target, done))

    def show_rec(self):
        self._ensure()
        self.set_status("● REC  0.0s", self.T["red"])
        self.set_partial("")
        self.set_sub("release to finish  ·  words appear live")
        self._place_top_center()
        self.win.deiconify()
        try:
            self.win.attributes("-alpha", 0.0)
        except Exception:
            pass
        self._fade(0.97)

    def show_text(self, text: str, color: str):
        self._ensure()
        self.set_status(text, color)
        self.set_sub("")
        self._place_top_center()
        self.win.deiconify()
        self._fade(0.97)

    def set_status(self, text: str, color: str):
        try:
            if self._status is not None:
                self._status.configure(text=text, text_color=color)
        except Exception:
            pass

    def set_partial(self, text: str):
        try:
            if self._partial is not None:
                short = text[-170:] if len(text) > 170 else text
                self._partial.configure(text=short)
        except Exception:
            pass

    def set_sub(self, text: str):
        try:
            if self._sub is not None:
                self._sub.configure(text=text)
        except Exception:
            pass

    def set_level(self, level: float):
        try:
            if self._bar is not None and self.win.winfo_viewable():
                self._bar.set(max(0.0, min(1.0, level)))
        except Exception:
            pass

    def hide_delayed(self, ms: int = 1500):
        if self._hide_after:
            try:
                self.root.after_cancel(self._hide_after)
            except Exception:
                pass
        self._hide_after = self.root.after(ms, self.hide)

    def hide(self):
        try:
            if self.win is not None and self.win.winfo_exists():
                self._fade(0.0, done=self.win.withdraw)
        except Exception:
            pass


class DraftApp:
    def __init__(self, root, start_minimized: bool = False):
        import customtkinter as ctk
        _beat("app-init-enter")
        self.root = root
        self.cfg = load_config()
        self.T = THEMES.get(self.cfg.get("theme", "golden"), THEMES["golden"])
        self.engines = EngineManager(self.cfg.get("engine_mode", "pro"))
        self.mic = MicRecorder()
        self.hotkeys = HotkeyManager(self._hotkey_hold_start,
                                     self._hotkey_hold_stop,
                                     self._hotkey_toggle,
                                     self._hotkey_dashboard)
        self.overlay = Overlay(root, self.T)
        self.tray = None
        self.stats = load_stats()
        self._boot = time.perf_counter()

        self.recording = False
        self.rec_started = 0.0
        self.busy = False
        self.history: list[dict] = self._load_history()
        self._partial_seq = 0
        self._partial_stop = threading.Event()
        self._fail_count = 0

        self._ui: queue.Queue = queue.Queue()
        self._state_lock = threading.Lock()

        try:
            icon = resource_path("icon.ico")
            if os.path.exists(icon):
                root.iconbitmap(icon)
        except Exception:
            pass
        self._build_window()
        _beat("window-built")
        self._refresh_stats()
        self._close_splash()
        _force_foreground(self.root)
        self._start_tray()
        if FROZEN and self.cfg.get("autostart", True) and not autostart_enabled():
            if set_autostart(True):
                self._append_log("Autostart enabled — Draft wakes with Windows.")
        if start_minimized or self.cfg.get("start_minimized"):
            self.root.withdraw()
        self._set_status("Loading speech engines…", self.T["yellow"])
        self._append_log("Draft starting — loading Pro engine (turbo)…")
        log.info("Draft v%s starting (frozen=%s)", APP_VERSION, FROZEN)

        threading.Thread(target=self._background_init, daemon=True).start()
        self.root.after(50, self._tick)

    # -- window ------------------------------------------------------------
    def _card(self, parent, **kw):
        import customtkinter as ctk
        T = self.T
        base = dict(fg_color=T["card"], corner_radius=18, border_width=1,
                    border_color=T["border"])
        base.update(kw)
        return ctk.CTkFrame(parent, **base)

    def _omenu(self, parent, variable, values, width=170):
        import customtkinter as ctk
        T = self.T
        return ctk.CTkOptionMenu(parent, variable=variable, values=values,
                                 width=width, fg_color=T["card2"],
                                 button_color=T["accent"],
                                 button_hover_color=T["accent_hover"],
                                 text_color=T["fg"],
                                 dropdown_fg_color=T["card2"],
                                 dropdown_hover_color=T["border"],
                                 dropdown_text_color=T["fg"])

    def _hlabel(self, parent, text, size=12):
        import customtkinter as ctk
        return ctk.CTkLabel(parent, text=text, text_color=self.T["muted"],
                            font=F_BODY(size))

    def _section(self, parent, text):
        import customtkinter as ctk
        return ctk.CTkLabel(parent, text=text.upper(), text_color=self.T["accent"],
                            font=("Segoe UI", 10, "bold"))

    def _build_window(self):
        import customtkinter as ctk
        T = self.T
        r = self.root
        r.title(f"{APP_NAME} — local speech to text")
        r.geometry("470x790")
        r.minsize(430, 640)
        r.configure(fg_color=T["bg"])
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass

        # Header — serif wordmark
        head = ctk.CTkFrame(r, fg_color="transparent")
        head.pack(fill="x", padx=20, pady=(16, 2))
        self.status_dot = ctk.CTkLabel(head, text="●", text_color=T["yellow"],
                                       font=("Segoe UI", 15))
        self.status_dot.pack(side="left")
        ctk.CTkLabel(head, text=f" {APP_NAME}  ", text_color=T["fg"],
                     font=F_HEAD(21)).pack(side="left")
        ctk.CTkLabel(head, text="golden hour edition", text_color=T["rose"],
                     font=("Georgia", 11, "italic")).pack(side="left",
                                                          pady=(7, 0))
        pill = ctk.CTkFrame(head, fg_color=T["card"], corner_radius=12,
                            border_width=1, border_color=T["border"])
        pill.pack(side="right")
        self.pill_dot = ctk.CTkLabel(pill, text="●", text_color=T["yellow"],
                                     font=("Segoe UI", 11))
        self.pill_dot.pack(side="left", padx=(10, 0))
        self.status_label = ctk.CTkLabel(pill, text="loading…",
                                         text_color=T["fg"],
                                         font=("Segoe UI", 11, "bold"))
        self.status_label.pack(side="left", padx=(4, 10), pady=5)
        self.engine_badge = ctk.CTkLabel(head, text="…", text_color=T["accent"],
                                         font=("Segoe UI", 10, "bold"),
                                         corner_radius=6, border_width=1,
                                         border_color=T["border"])
        self.engine_badge.pack(side="right", padx=(0, 8))

        # Tab bar — Dictate / History / Settings
        tabbar = ctk.CTkFrame(r, fg_color="transparent")
        tabbar.pack(fill="x", padx=16, pady=(2, 6))
        self._tab_btns = {}
        self._tab_frames = {}
        for _name in ("Dictate", "History", "Settings"):
            _b = ctk.CTkButton(tabbar, text=_name, height=34,
                               fg_color=T["card"], hover_color=T["card2"],
                               text_color=T["muted"], corner_radius=10,
                               font=("Segoe UI", 12, "bold"),
                               command=lambda n=_name: self._show_tab(n))
            _b.pack(side="left", expand=True, fill="x", padx=3)
            self._tab_btns[_name] = _b
            _f = ctk.CTkFrame(r, fg_color="transparent")
            self._tab_frames[_name] = _f
        tabD = ctk.CTkScrollableFrame(self._tab_frames["Dictate"],
                                      fg_color="transparent")
        tabD.pack(fill="both", expand=True)
        tabH = self._tab_frames["History"]
        tabS = ctk.CTkScrollableFrame(self._tab_frames["Settings"],
                                      fg_color="transparent")
        tabS.pack(fill="both", expand=True)
        self._show_tab("Dictate")

        # Talk card — circular hero button + living waveform
        card = self._card(tabD)
        card.pack(fill="x", padx=16, pady=8)
        import tkinter as tk
        from PIL import ImageTk
        self._mic_art = MicArt(T)
        self._mic_photo = self._mic_art.idle_photo()
        self.mic_icon = tk.Label(card, image=self._mic_photo, bg=T["card"],
                                 cursor="hand2")
        self.mic_icon.pack(pady=(10, 0))
        self.mic_icon.bind("<ButtonPress-1>", self._button_hold_start)
        self.mic_icon.bind("<ButtonRelease-1>", self._button_hold_stop)
        self._mic_state = "idle"
        self._mic_phase = 0.0
        self._wave_phase = 0.0
        self.mic_button = ctk.CTkLabel(card, text="HOLD TO TALK",
                                       text_color=T["accent"],
                                       font=("Segoe UI", 12, "bold"))
        self.mic_button.pack(pady=(0, 2))
        self.hero_line = ctk.CTkLabel(card, text="Ready to write",
                                      text_color=T["muted"],
                                      font=("Georgia", 14, "italic"))
        self.hero_line.pack(pady=(0, 2))
        self._btn_down = False
        self._btn_press_ms: int | None = None
        self._hlabel(card, "Hold the circle, speak, release.   F9 toggles.",
                     11).pack(pady=(0, 2))
        self.timer_label = ctk.CTkLabel(card, text="", text_color=T["muted"],
                                        font=F_BODY(11))
        self.timer_label.pack(pady=(0, 6))
        self.level_bar = None
        self.wave = tk.Canvas(card, width=380, height=76, bg=T["card"],
                              highlightthickness=0)
        self.wave.pack(pady=(0, 12))
        self._draw_wave()

        # Result card
        res = self._card(tabD)
        res.pack(fill="x", padx=16, pady=4)
        self._section(res, "Last result").pack(anchor="w", padx=14,
                                                  pady=(10, 0))
        ctk.CTkFrame(res, height=2, fg_color=T["accent"]).pack(
            fill="x", padx=14, pady=(4, 0))
        self.result_box = ctk.CTkTextbox(res, height=66, corner_radius=10,
                                          border_width=1,
                                          border_color=T["border"],
                                          fg_color="#ece1d0",
                                          text_color="#241a10",
                                          font=("Segoe UI", 13))
        self.result_box.pack(fill="x", padx=12, pady=6)
        brow = ctk.CTkFrame(res, fg_color="transparent")
        brow.pack(fill="x", padx=12, pady=(0, 4))
        for label, cmd in (("Copy", self._copy_result),
                           ("Paste", self._paste_result),
                           ("WAV…", self._transcribe_file)):
            ctk.CTkButton(brow, text=label, width=82, height=28,
                          fg_color="transparent", hover_color=T["card2"],
                          border_width=1, border_color=T["border"],
                          text_color=T["fg"], font=F_BODY(11),
                          command=cmd).pack(side="left", padx=(0, 8))
        self.timing_label = ctk.CTkLabel(res, text="", text_color=T["muted"],
                                         font=F_MONO(10),
                                         anchor="w", justify="left")
        self.timing_label.pack(fill="x", padx=14, pady=(0, 10))

        # Compose card (only in compose mode)
        self.compose_card = self._card(tabD)
        self._section(self.compose_card, "Compose  — takes gather here"
                        ).pack(anchor="w", padx=14, pady=(10, 0))
        self.compose_box = ctk.CTkTextbox(self.compose_card, height=70,
                                          corner_radius=10, border_width=1,
                                          border_color=T["border"],
                                          fg_color=T["card2"],
                                          text_color=T["fg"],
                                          font=F_BODY(12))
        self.compose_box.pack(fill="x", padx=12, pady=6)
        crow = ctk.CTkFrame(self.compose_card, fg_color="transparent")
        crow.pack(fill="x", padx=12, pady=(0, 10))
        for label, cmd in (("Paste all", self._compose_paste),
                           ("Copy all", self._compose_copy),
                           ("Clear", self._compose_clear)):
            ctk.CTkButton(crow, text=label, width=82, height=28,
                          fg_color=T["card2"], hover_color=T["border"],
                          text_color=T["fg"], font=F_BODY(11),
                          command=cmd).pack(side="left", padx=(0, 8))
        if self.cfg.get("paste_mode") == "compose":
            self.compose_card.pack(fill="x", padx=16, pady=4)

        # Stats tiles + week chart
        tiles = ctk.CTkFrame(tabD, fg_color="transparent")
        tiles.pack(fill="x", padx=16, pady=4)
        self._tiles = {}
        _names = (("words", "words"), ("takes", "takes"),
                  ("saved", "min saved"), ("streak", "day streak"))
        for _i, (_key, _cap) in enumerate(_names):
            if _i > 0:
                _div = ctk.CTkFrame(tiles, width=1, fg_color=T["border"])
                _div.pack(side="left", fill="y", padx=8, pady=12)
            _f = ctk.CTkFrame(tiles, fg_color="transparent")
            _f.pack(side="left", expand=True, fill="x")
            _num = ctk.CTkLabel(_f, text="0", text_color=T["accent"],
                                font=("Georgia", 24, "bold"))
            _num.pack()
            ctk.CTkLabel(_f, text=_cap, text_color=T["muted"],
                         font=F_BODY(10)).pack()
            self._tiles[_key] = _num
        week = self._card(tabD)
        week.pack(fill="x", padx=16, pady=4)
        self._section(week, "This week  ·  words per day").pack(
            anchor="w", padx=14, pady=(10, 0))
        self.week_canvas = tk.Canvas(week, width=380, height=96, bg=T["card"],
                                     highlightthickness=0)
        self.week_canvas.pack(padx=14, pady=(2, 10))
        self._draw_week()

        # History card
        hist = self._card(tabH)
        hist.pack(fill="both", expand=True, padx=16, pady=4)
        hhead = ctk.CTkFrame(hist, fg_color="transparent")
        hhead.pack(fill="x", padx=14, pady=(10, 2))
        self._section(hhead, "History  ·  click an item to copy it"
                        ).pack(side="left")
        ctk.CTkButton(hhead, text="Export", width=64, height=24,
                      fg_color=T["card2"], hover_color=T["border"],
                      text_color=T["fg"], font=F_BODY(10),
                      command=self._export_history).pack(side="right")
        self.history_frame = ctk.CTkScrollableFrame(hist, height=96,
                                                     fg_color="transparent")
        self.history_frame.pack(fill="both", expand=True, padx=8, pady=6)
        self._refresh_history()

        # Settings card
        setup = self._card(tabS)
        setup.pack(fill="x", padx=16, pady=(4, 6))
        self._section(setup, "Settings").pack(anchor="w", padx=14,
                                                 pady=(8, 2))
        grid = ctk.CTkFrame(setup, fg_color="transparent")
        grid.pack(fill="x", padx=14, pady=2)
        grid.columnconfigure(1, weight=1)

        self.var_theme = ctk.StringVar(value=self.cfg.get("theme", "golden"))
        self.var_mode = ctk.StringVar(value=self.cfg.get("engine_mode", "pro"))
        self.var_paste = ctk.StringVar(value=self.cfg.get("paste_mode", "direct"))
        self.var_lang = ctk.StringVar(value=self.cfg.get("language", "auto"))
        self.var_mic = ctk.StringVar(value=str(self.cfg.get("mic", "default")))
        self.var_hold = ctk.StringVar(value=self.cfg.get("hotkey_hold", "right ctrl"))
        self.var_toggle = ctk.StringVar(value=self.cfg.get("hotkey_toggle", "f9"))
        self.var_dashkey = ctk.StringVar(value=self.cfg.get("hotkey_dashboard", "ctrl+alt+d"))
        self.var_autopaste = ctk.BooleanVar(value=bool(self.cfg.get("auto_paste", True)))
        self.var_clipboard = ctk.BooleanVar(value=bool(self.cfg.get("copy_to_clipboard", True)))
        self.var_partials = ctk.BooleanVar(value=bool(self.cfg.get("live_partials", True)))
        self.var_commands = ctk.BooleanVar(value=bool(self.cfg.get("voice_commands", True)))
        self.var_sounds = ctk.BooleanVar(value=bool(self.cfg.get("sounds", True)))
        self.var_fillers = ctk.BooleanVar(value=bool(self.cfg.get("remove_fillers", True)))
        self.var_dots = ctk.BooleanVar(value=bool(self.cfg.get("smart_dots", True)))
        self.var_autostart = ctk.BooleanVar(value=bool(self.cfg.get("autostart", True)))
        self.var_minimized = ctk.BooleanVar(value=bool(self.cfg.get("start_minimized", False)))

        self.mic_menu = self._omenu(grid, variable=self.var_mic,
                                      values=["default"], width=170)
        rows = (
            ("Theme", self._omenu(grid, variable=self.var_theme,
                                   values=list(THEME_NAMES), width=170)),
            ("Engine  (pro · flash)",
             self._omenu(grid, variable=self.var_mode,
                          values=list(ENGINE_MODES), width=170)),
            ("Paste  (direct · compose)",
             self._omenu(grid, variable=self.var_paste,
                          values=list(PASTE_MODES), width=170)),
            ("Language",
             self._omenu(grid, variable=self.var_lang,
                          values=list(LANGUAGES), width=170)),
            ("Microphone", self.mic_menu),
            ("Hold hotkey", ctk.CTkEntry(grid, textvariable=self.var_hold,
                                         width=170)),
            ("Toggle hotkey", ctk.CTkEntry(grid, textvariable=self.var_toggle,
                                           width=170)),
            ("Dashboard hotkey", ctk.CTkEntry(grid, textvariable=self.var_dashkey,
                                           width=170)),
        )
        for i, (label, widget) in enumerate(rows):
            ctk.CTkLabel(grid, text=label, text_color=T["muted"],
                         font=F_BODY(11)).grid(row=i, column=0, sticky="w",
                                               pady=3)
            widget.grid(row=i, column=1, sticky="e", pady=3, padx=(8, 0))

        # Hotwords manager
        hw = ctk.CTkFrame(setup, fg_color="transparent")
        hw.pack(fill="x", padx=14, pady=(4, 2))
        ctk.CTkLabel(hw, text="✎  Hotwords  ·  names and products, biased at decode",
                     text_color=T["muted"], font=F_BODY(11)).pack(anchor="w")
        hwrow = ctk.CTkFrame(hw, fg_color="transparent")
        hwrow.pack(fill="x", pady=(4, 2))
        self.hw_entry = ctk.CTkEntry(hwrow, width=200,
                                     placeholder_text="Add a name…")
        self.hw_entry.pack(side="left", padx=(0, 8))
        self.hw_entry.bind("<Return>", lambda _e: self._hw_add())
        ctk.CTkButton(hwrow, text="Add", width=64, height=28,
                      fg_color=T["card2"], hover_color=T["border"],
                      text_color=T["fg"], font=F_BODY(11),
                      command=self._hw_add).pack(side="left")
        self.hw_chips = ctk.CTkFrame(hw, fg_color="transparent")
        self.hw_chips.pack(fill="x", pady=(2, 2))
        self._refresh_hw_chips()

        # Taught words — guaranteed corrections
        tw = ctk.CTkFrame(setup, fg_color="transparent")
        tw.pack(fill="x", padx=14, pady=(4, 2))
        ctk.CTkLabel(tw, text="★  Taught words  ·  what it hears → what you mean",
                     text_color=T["muted"], font=F_BODY(11)).pack(anchor="w")
        twrow = ctk.CTkFrame(tw, fg_color="transparent")
        twrow.pack(fill="x", pady=(4, 2))
        self.taught_hears = ctk.CTkEntry(twrow, width=130,
                                         placeholder_text="Hears: tiroop")
        self.taught_hears.pack(side="left", padx=(0, 6))
        self.taught_writes = ctk.CTkEntry(twrow, width=130,
                                          placeholder_text="Write: Tirup")
        self.taught_writes.pack(side="left", padx=(0, 8))
        self.taught_hears.bind("<Return>", lambda _e: self._taught_add())
        self.taught_writes.bind("<Return>", lambda _e: self._taught_add())
        ctk.CTkButton(twrow, text="Teach", width=64, height=28,
                      fg_color=T["accent"], hover_color=T["accent_hover"],
                      text_color="#1a120a", font=F_BODY(11),
                      command=self._taught_add).pack(side="left")
        self.taught_chips = ctk.CTkFrame(tw, fg_color="transparent")
        self.taught_chips.pack(fill="x", pady=(2, 2))
        self._refresh_taught_chips()

        chk = ctk.CTkFrame(setup, fg_color="transparent")
        chk.pack(fill="x", padx=14, pady=4)
        for text, var in (("Auto-paste", self.var_autopaste),
                          ("Clipboard", self.var_clipboard),
                          ("Live words", self.var_partials),
                          ("Commands", self.var_commands),
                          ("No fillers", self.var_fillers),
                          ("Smart dots", self.var_dots),
                          ("Sounds", self.var_sounds),
                          ("Autostart", self.var_autostart),
                          ("Start in tray", self.var_minimized)):
            ctk.CTkSwitch(chk, text=text, variable=var,
                          progress_color=T["accent"],
                          font=F_BODY(11)).pack(side="left", padx=(0, 8),
                                                pady=2)
        brow2 = ctk.CTkFrame(setup, fg_color="transparent")
        brow2.pack(fill="x", padx=14, pady=(2, 10))
        ctk.CTkButton(brow2, text="Reload engines", width=120, height=30,
                      fg_color=T["card2"], hover_color=T["border"],
                      text_color=T["fg"], font=F_BODY(11),
                      command=self._reload_engines).pack(side="left")
        ctk.CTkButton(brow2, text="Logs", width=80, height=30,
                      fg_color=T["card2"], hover_color=T["border"],
                      text_color=T["fg"], font=F_BODY(11),
                      command=self._open_logs).pack(side="left", padx=(8, 0))
        ctk.CTkButton(brow2, text="Save settings", width=120, height=30,
                      fg_color=T["accent"], hover_color=T["accent_hover"],
                      text_color="#1a120a", font=("Segoe UI", 12, "bold"),
                      command=self._save_settings).pack(side="right")

        self.log_label = None  # logs live in the log file, not the UI

        r.protocol("WM_DELETE_WINDOW", self._on_close)
        try:
            r.attributes("-alpha", 0.0)
            self._fade_window_in()
        except Exception:
            pass

    def _fade_window_in(self, alpha: float = 0.0):
        try:
            if not self.root.winfo_exists():
                return
            alpha = min(1.0, alpha + 0.12)
            self.root.attributes("-alpha", alpha)
            if alpha < 1.0:
                self.root.after(18, lambda: self._fade_window_in(alpha))
        except Exception:
            pass

    # -- init --------------------------------------------------------------
    def _background_init(self):
        ok = self.engines.load(status=lambda m: self._call_in_ui(
            self._append_log, m))
        mics = self.mic.list_inputs()
        names = [str(d.get("name", "?")) for d in mics]

        def _done():
            try:
                import faulthandler as _fh2
                _fh2.cancel_dump_traceback_later()
            except Exception:
                pass
            if ok:
                self._set_status(f"Ready · {self.engines.describe()}",
                                 self.T["green"])
                self._append_log(
                    f"engine: {self.engines.describe()}. "
                    f"hold {self.cfg.get('hotkey_hold')} + speak.")
                self._refresh_badge()
                self._start_hotkeys()
                self._refresh_stats()
            else:
                self._set_status("Engines failed to load", self.T["red"])
                self._append_log(f"ERROR: {self.engines.error}")
            try:
                vals = ["default"] + names[:8]
                self.mic_menu.configure(values=vals)
                if names:
                    self._append_log("mics: " + " | ".join(names[:4]))
                else:
                    self._append_log("WARNING: no microphone found.")
            except Exception:
                pass
            self._close_splash()
            if not self.cfg.get("welcomed"):
                self.cfg["welcomed"] = True
                save_config(self.cfg)
                self._show_welcome()
        self._call_in_ui(_done)

    def _close_splash(self):
        if getattr(self, "_splash_closed", False):
            return
        self._splash_closed = True
        try:
            import pyi_splash  # type: ignore
            try:
                pyi_splash.update_text("Opening Draft…")
            except Exception:
                pass
            pyi_splash.close()
            log.info("splash closed")
        except Exception:
            pass

    def _start_hotkeys(self):
        ok = self.hotkeys.start(self.cfg.get("hotkey_hold", "right ctrl"),
                                self.cfg.get("hotkey_toggle", "f9"),
                                self.cfg.get("hotkey_dashboard", "ctrl+alt+d"))
        if ok:
            self._append_log(
                f"hotkeys: hold [{self.hotkeys.hold_key}] · "
                f"toggle [{self.hotkeys.toggle_key}] · "
                f"dashboard [{self.hotkeys.dashboard_key}] · 24/7 in tray")
        else:
            self._append_log("WARNING: global hotkeys unavailable. "
                             "Button still works.")

    def _start_tray(self):
        try:
            import pystray
        except ImportError:
            return
        if self.tray is not None:
            return
        try:
            from PIL import Image
            tray_png = resource_path("tray.png")
            image = Image.open(tray_png) if os.path.exists(tray_png) \
                else make_tray_image_fallback()
            menu = pystray.Menu(
                pystray.MenuItem("Open Draft",
                                 lambda: self._call_in_ui(self._show_window),
                                 default=True),
                pystray.MenuItem(f"v{APP_VERSION} · local",
                                 lambda: None, enabled=False),
                pystray.MenuItem("Quit Draft",
                                 lambda: self._call_in_ui(self._quit)),
            )
            self.tray = pystray.Icon("Draft", image,
                                     "Draft — local speech to text", menu)
            threading.Thread(target=self.tray.run, daemon=True).start()
            log.info("tray started")
        except Exception:
            log.exception("tray")

    def _show_tab(self, name: str):
        try:
            for n, f in self._tab_frames.items():
                if n == name:
                    f.pack(fill="both", expand=True, padx=0, pady=0)
                else:
                    f.pack_forget()
            for n, b in self._tab_btns.items():
                if n == name:
                    b.configure(fg_color=self.T["accent"],
                                text_color="#1a120a")
                else:
                    b.configure(fg_color=self.T["card"],
                                text_color=self.T["muted"])
        except Exception:
            pass

    def _draw_mic(self):
        """Swap the hero art: cached idle disc, live rings when recording."""
        try:
            from PIL import ImageTk
            if self._mic_state == "recording":
                self._mic_phase += 0.35
                img = self._mic_art.frame(self._mic_phase)
            else:
                img = self._mic_art.idle()
            self._mic_photo = ImageTk.PhotoImage(img)
            self.mic_icon.configure(image=self._mic_photo)
        except Exception:
            pass

    def _draw_wave(self):
        try:
            import math
            c, T = self.wave, self.T
            c.delete("all")
            W, H = 380, 76
            if self.recording:
                peaks = self.mic.peaks_snapshot()
                if len(peaks) >= 2:
                    data = peaks[-140:]
                    step = W / 140
                    for i, v in enumerate(data):
                        h = max(2.0, float(v) * (H - 12))
                        x = i * step
                        c.create_line(x, H / 2 - h / 2, x, H / 2 + h / 2,
                                      fill=T["accent"], width=3)
                    return
            self._wave_phase = getattr(self, "_wave_phase", 0.0) + 0.18
            pts = []
            x = 0.0
            while x <= W:
                y = H / 2 + math.sin(x * 0.045 + self._wave_phase) * 6
                pts += [x, y]
                x += 4
            c.create_line(*pts, fill=T["dim"], width=2, smooth=True)
        except Exception:
            pass

    def _draw_week(self):
        try:
            import datetime
            c, T = self.week_canvas, self.T
            c.delete("all")
            W, H = 380, 96
            days = self.stats.get("days", {})
            today = datetime.date.today()
            vals = []
            for i in range(6, -1, -1):
                dd = today - datetime.timedelta(days=i)
                vals.append((dd.strftime("%a")[0],
                             days.get(dd.isoformat(), {}).get("w", 0)))
            if sum(v for _, v in vals) == 0:
                c.create_text(W / 2, H / 2 - 6, text="dictate to light up",
                              fill=T["muted"], font=("Georgia", 12, "italic"))
                c.create_text(W / 2, H / 2 + 12, text="your week",
                              fill=T["muted"], font=("Georgia", 12, "italic"))
                return
            mx = max([v for _, v in vals] + [1])
            bw = W / 7
            for i, (lbl, v) in enumerate(vals):
                h = max(4.0, (v / mx) * (H - 34))
                x0 = i * bw + bw * 0.30
                x1 = (i + 1) * bw - bw * 0.30
                col = T["accent"] if i == 6 else T["glow"]
                c.create_rectangle(x0, H - 20 - h, x1, H - 20, fill=col,
                                   outline="")
                if v > 0:
                    c.create_text((x0 + x1) / 2, H - 24 - h, text=str(v),
                                  fill=T["fg"], font=("Segoe UI", 8, "bold"))
                c.create_text((x0 + x1) / 2, H - 9, text=lbl, fill=T["muted"],
                              font=("Segoe UI", 8))
        except Exception:
            pass

    def _streak(self) -> int:
        import datetime
        try:
            days = self.stats.get("days", {})
            d = datetime.date.today()
            if days.get(d.isoformat(), {}).get("t", 0) == 0:
                d -= datetime.timedelta(days=1)
            s = 0
            while days.get(d.isoformat(), {}).get("t", 0) > 0:
                s += 1
                d -= datetime.timedelta(days=1)
            return s
        except Exception:
            return 0

    def _show_welcome(self):
        import customtkinter as ctk
        T = self.T
        try:
            w = ctk.CTkToplevel(self.root)
            w.title("Welcome to Draft")
            w.geometry("380x300")
            w.attributes("-topmost", True)
            w.configure(fg_color=T["card"])
            ctk.CTkLabel(w, text="Draft", text_color=T["accent"],
                         font=F_HEAD(28)).pack(pady=(20, 2))
            ctk.CTkLabel(w, text="hold  ·  speak  ·  done",
                         text_color=T["muted"],
                         font=F_BODY(12)).pack(pady=(0, 12))
            for line in (f"1  Hold  {self.cfg.get('hotkey_hold')}  and speak",
                         "2  Release — text lands at your cursor",
                         '3  Say “scratch that” to undo a take',
                         f"4  {self.cfg.get('hotkey_dashboard', 'ctrl+alt+d')} summons this window"):
                ctk.CTkLabel(w, text=line, text_color=T["fg"],
                             font=F_BODY(12)).pack(anchor="w", padx=36,
                                                   pady=3)
            ctk.CTkButton(w, text="Start dictating", fg_color=T["accent"],
                          hover_color=T["accent_hover"], text_color="#1a120a",
                          font=("Segoe UI", 13, "bold"), height=40,
                          command=w.destroy).pack(pady=16)
        except Exception:
            log.exception("welcome")

    # -- recording ---------------------------------------------------------
    def start_recording(self, source: str = "button") -> bool:
        with self._state_lock:
            if not self.engines.ready or self.busy or self.recording:
                if not self.engines.ready:
                    self._call_in_ui(self._append_log,
                                     "Engines still loading — one moment…")
                return False
            try:
                self.mic.start(self.cfg.get("mic", "default"))
            except Exception as exc:  # noqa: BLE001
                self._call_in_ui(self._append_log, f"Mic error: {exc}")
                return False
            self.recording = True
            self.rec_started = time.perf_counter()
            self._partial_seq += 1
            self._partial_stop.clear()
            seq = self._partial_seq
        if self.cfg.get("sounds", True):
            beep(740, 55)
        self._call_in_ui(self._ui_rec_started)
        if self.cfg.get("live_partials", True):
            threading.Thread(target=self._partial_worker, args=(seq,),
                             daemon=True).start()
        return True

    def _hero(self, text: str):
        try:
            self.hero_line.configure(text=text)
        except Exception:
            pass

    def _ui_rec_started(self):
        if self.cfg.get("show_overlay", True):
            self.overlay.show_rec()
        self._mic_state = "recording"
        self._draw_mic()
        self._hero("Listening…")
        try:
            self.mic_button.configure(text="●  RECORDING — RELEASE TO FINISH")
        except Exception:
            pass
        self._set_status("Listening…", self.T["red"])

    def stop_and_transcribe(self):
        with self._state_lock:
            if not self.recording:
                return
            self.recording = False
            held = time.perf_counter() - self.rec_started
            self._partial_stop.set()
        audio, _sr = self.mic.stop()
        if self.cfg.get("sounds", True):
            beep(520, 55)
        if held < 0.25:
            self._call_in_ui(self._ui_rec_too_short)
            return
        with self._state_lock:
            self.busy = True
        self._call_in_ui(self._ui_transcribing)
        threading.Thread(target=self._transcribe_worker,
                         args=(audio,), daemon=True).start()

    def _ui_rec_too_short(self):
        self._mic_state = "idle"
        self._draw_mic()
        try:
            self.mic_button.configure(text="HOLD TO TALK")
        except Exception:
            pass
        self._append_log("Too short — hold a beat longer while you speak.")
        self.overlay.hide()
        self._set_status_ready()

    def _ui_transcribing(self):
        self._mic_state = "idle"
        self._draw_mic()
        self._hero("Transcribing…")
        try:
            self.mic_button.configure(text="HOLD TO TALK")
        except Exception:
            pass
        if self.cfg.get("show_overlay", True):
            self.overlay.show_text("Transcribing…", self.T["yellow"])
        self._set_status("Transcribing…", self.T["yellow"])

    # -- live partials -------------------------------------------------------
    def _partial_worker(self, seq: int):
        last_shown = ""
        pro = self.engines.mode == "pro"
        while not self._partial_stop.wait(0.9):
            with self._state_lock:
                if not self.recording or seq != self._partial_seq:
                    return
            try:
                audio, _sr = self.mic.snapshot()
                import numpy as np
                if audio.size < SAMPLE_RATE * 0.7:
                    continue
                text = self.engines.partial(
                    audio, language=lang_or_none(
                        self.cfg.get("language", "auto"), pro_engine=pro),
                    hotwords=parse_hotwords(self.cfg.get("hotwords", "")),
                    corrections=self.cfg.get("corrections", []))
                if text and text != last_shown:
                    last_shown = text
                    self._call_in_ui(self._show_partial, seq, text)
            except Exception:
                pass

    def _show_partial(self, seq: int, text: str):
        if seq != self._partial_seq or not self.recording:
            return
        if self.cfg.get("show_overlay", True):
            self.overlay.set_partial(text)

    # -- final transcription ---------------------------------------------------
    def _transcribe_worker(self, audio):
        import numpy as np
        try:
            audio = np.asarray(audio, dtype=np.float32).ravel()
            clean, had_speech = preprocess(audio)
            if clean.size == 0 or (not had_speech
                                   and clean.size < SAMPLE_RATE // 4):
                self._call_in_ui(self._on_empty, "No speech detected.")
                return
            pro = self.engines.mode == "pro"
            result = self.engines.transcribe(
                clean,
                language=lang_or_none(self.cfg.get("language", "auto"),
                                      pro_engine=pro),
                hotwords=parse_hotwords(self.cfg.get("hotwords", "")),
                corrections=self.cfg.get("corrections", []))
            text = (result.get("text") or "").strip()
            kind = "text"
            if self.cfg.get("voice_commands", True) and text:
                kind, payload = apply_voice_commands(text)
                if kind == "text":
                    text = payload
            if kind == "scratch":
                self._call_in_ui(self._on_scratch, text)
                return
            if kind == "paste":
                self._call_in_ui(self._on_command_paste, payload, text)
                return
            text = apply_corrections(text, self.cfg.get("corrections", []),
                                     smart_dots=bool(
                                         self.cfg.get("smart_dots", True)))
            if self.cfg.get("remove_fillers", True):
                text = remove_fillers(text)
            if self.cfg.get("clean_output", True):
                text = clean_text(
                    text, trailing_space=bool(
                        self.cfg.get("trailing_space", True)))
            elif self.cfg.get("trailing_space", True) and text \
                    and not text.endswith(" "):
                text += " "
            with self._state_lock:
                self._fail_count = 0
            self._call_in_ui(self._on_result, text, result)
        except Exception as exc:  # noqa: BLE001
            log.exception("transcribe")
            with self._state_lock:
                self._fail_count += 1
                fails = self._fail_count
            self._call_in_ui(self._on_error, f"Transcribe error: {exc}")
            if fails >= 3 and not self.engines._reloading:
                self._call_in_ui(
                    self._append_log,
                    "3 failures in a row — reloading engines…")
                threading.Thread(target=self._watchdog_reload,
                                 daemon=True).start()

    def _watchdog_reload(self):
        ok = self.engines.reload(status=lambda m: self._call_in_ui(
            self._append_log, m))
        with self._state_lock:
            self._fail_count = 0
        self._call_in_ui(self._append_log,
                          "Engines reloaded."
                          if ok else "Reload failed — restart Draft.")
        self._call_in_ui(self._set_status_ready)

    def _reload_engines(self):
        self._append_log("Reloading engines…")
        threading.Thread(target=self._watchdog_reload, daemon=True).start()

    def _open_logs(self):
        try:
            os.startfile(DATA_DIR)
        except Exception as exc:
            self._append_log(f"Cannot open folder: {exc}")

    # -- result handling -------------------------------------------------------
    def _bump_stats(self, text: str):
        import datetime
        words = len(text.split())
        self.stats["dictations"] = int(self.stats.get("dictations", 0)) + 1
        self.stats["words"] = int(self.stats.get("words", 0)) + words
        self.stats["chars"] = int(self.stats.get("chars", 0)) + len(text)
        try:
            days = self.stats.setdefault("days", {})
            rec = days.setdefault(datetime.date.today().isoformat(),
                                  {"w": 0, "t": 0})
            rec["w"] = int(rec.get("w", 0)) + words
            rec["t"] = int(rec.get("t", 0)) + 1
            for k in sorted(days)[:-30]:
                del days[k]
        except Exception:
            pass
        save_stats(self.stats)
        self._refresh_stats()

    def _refresh_stats(self):
        try:
            d = int(self.stats.get("dictations", 0))
            w = int(self.stats.get("words", 0))
            saved = w * (1 / 40 - 1 / 130)  # typing vs speaking, minutes
            self._tiles["words"].configure(text=f"{w:,}")
            self._tiles["takes"].configure(text=f"{d}")
            self._tiles["saved"].configure(text=f"{saved:.0f}")
            self._tiles["streak"].configure(text=f"{self._streak()}")
            self._draw_week()
        except Exception:
            pass

    def _on_result(self, text: str, result: dict):
        with self._state_lock:
            self.busy = False
        if not text.strip():
            self._on_empty("No speech detected.")
            return
        self._bump_stats(text)
        self._push_history(text, result.get("language") or "?")
        self._hero("✓  " + (text[:42] if len(text) > 42 else text))
        if self.cfg.get("paste_mode") == "compose":
            self._compose_append(text)
            self._set_result_text(text)
            pasted = False
        else:
            self._set_result_text(text)
            pasted = False
            if self.cfg.get("auto_paste", True):
                pasted = paste_text(text)
                time.sleep(0.02)
        if self.cfg.get("copy_to_clipboard", True) and \
                self.cfg.get("paste_mode") == "direct":
            try:
                import pyperclip
                pyperclip.copy(text)
            except Exception:
                pass
        total = result.get("total_ms", 0.0)
        rtf = result.get("rtf", 0.0)
        lang = result.get("language") or "?"
        extra = " · truncated" if result.get("truncated") else ""
        detail = ""
        if result.get("ttft_ms") is not None:
            detail = (f" · tok {result.get('ttft_ms', 0):.0f} ms · "
                      f"{result.get('decode_tps', 0):.0f} t/s")
        self.timing_label.configure(
            text=f"{lang} · {total:.0f} ms · {rtf:.1f}x realtime"
                 f"{detail}{extra} · {self.engines.describe()}")
        if self.cfg.get("show_overlay", True):
            if self.cfg.get("paste_mode") == "compose":
                mark = "✓ Composed"
            else:
                mark = "✓ Pasted" if pasted else "✓ Copied"
            self.overlay.show_text(f"{mark}  ·  {text[:44]}",
                                   self.T["green"])
            self.overlay.hide_delayed(1500)
        else:
            self.overlay.hide()
        self._append_log(f"✓ “{text[:80]}”")
        self._set_status_ready()

    def _on_scratch(self, heard: str):
        with self._state_lock:
            self.busy = False
        self.overlay.hide()
        self._append_log(f"↩ scratched “{heard[:60]}” — nothing pasted.")
        self._set_status_ready()

    def _on_command_paste(self, payload: str, heard: str):
        with self._state_lock:
            self.busy = False
        self.overlay.hide()
        if payload:
            paste_text(payload, restore_clipboard=False)
        self._append_log(f"↵ command “{heard[:60]}” applied.")
        self._set_status_ready()

    def _on_empty(self, msg: str):
        with self._state_lock:
            self.busy = False
        self.overlay.hide()
        self._hero("Ready to write")
        self._append_log(msg)
        self._set_status_ready()

    def _on_error(self, msg: str):
        with self._state_lock:
            self.busy = False
        self.overlay.hide()
        self._hero("Ready to write")
        self._append_log("ERROR: " + msg)

    def _set_status_ready(self):
        with self._state_lock:
            self.busy = False
        if self.engines.ready:
            self._set_status(f"Ready · {self.engines.describe()}",
                             self.T["green"])
        self._refresh_badge()

    def _refresh_badge(self):
        try:
            self.engine_badge.configure(text=self.engines.describe())
        except Exception:
            pass

    # -- compose -------------------------------------------------------------------
    def _compose_append(self, text: str):
        try:
            cur = self.compose_box.get("1.0", "end").strip()
            sep = " " if cur and not cur.endswith("\n") else ""
            self.compose_box.delete("1.0", "end")
            self.compose_box.insert("1.0", cur + sep + text.strip())
            self._append_log("→ composed (Paste all when ready).")
        except Exception:
            pass

    def _compose_text(self) -> str:
        try:
            return self.compose_box.get("1.0", "end").strip()
        except Exception:
            return ""

    def _compose_paste(self):
        if paste_text(self._compose_text()):
            self._append_log("Composed text pasted.")

    def _compose_copy(self):
        try:
            import pyperclip
            pyperclip.copy(self._compose_text())
            self._append_log("Composed text copied.")
        except Exception as exc:
            self._append_log(f"Copy failed: {exc}")

    def _compose_clear(self):
        try:
            self.compose_box.delete("1.0", "end")
        except Exception:
            pass

    # -- hotkeys (listener threads; mic ops are thread-safe) -------------------------
    def _hotkey_hold_start(self):
        self.start_recording("hotkey")

    def _hotkey_hold_stop(self):
        self.stop_and_transcribe()

    def _hotkey_toggle(self):
        with self._state_lock:
            rec = self.recording
        if rec:
            self.stop_and_transcribe()
        else:
            self.start_recording("toggle")

    def _hotkey_dashboard(self):
        # Listener thread → marshal to the UI thread.
        self._call_in_ui(self._show_window)

    # -- in-app button -------------------------------------------------------------------
    def _button_hold_start(self, event=None):
        with self._state_lock:
            rec, busy, started = (self.recording, self.busy, self.rec_started)
        if rec and not self._btn_down and \
                (time.perf_counter() - started) > 0.35 and not busy:
            self.stop_and_transcribe()
            return
        if rec:
            return
        self._btn_down = True
        try:
            self._btn_press_ms = int(event.time) if event is not None else None
        except Exception:
            self._btn_press_ms = None
        self.start_recording("button")

    def _button_hold_stop(self, event=None):
        was_hold = self._btn_down
        self._btn_down = False
        with self._state_lock:
            rec = self.recording
        if not rec:
            return
        held = None
        try:
            if event is not None and self._btn_press_ms is not None:
                held = (int(event.time) - self._btn_press_ms) / 1000.0
        except Exception:
            held = None
        if held is None:
            held = time.perf_counter() - self.rec_started
        if was_hold and held < 0.35:
            try:
                self.mic_button.configure(
                    text="■  Recording… click again to stop")
            except Exception:
                pass
            return
        self.stop_and_transcribe()

    # -- UI helpers ------------------------------------------------------------------------
    def _call_in_ui(self, fn, *args, **kwargs):
        try:
            self._ui.put_nowait(lambda: fn(*args, **kwargs))
        except Exception:
            pass

    def _drain_ui_queue(self):
        while True:
            try:
                fn = self._ui.get_nowait()
            except queue.Empty:
                return
            try:
                fn()
            except Exception:
                traceback.print_exc()

    def _set_status(self, text: str, color: str):
        try:
            self.status_label.configure(text=text)
            self.status_dot.configure(text_color=color)
            self.pill_dot.configure(text_color=color)
        except Exception:
            pass

    def _append_log(self, msg: str):
        try:
            log.info(msg)
        except Exception:
            pass
        try:
            if self.log_label is None:
                return
            lines = (self.log_label.cget("text") or "").splitlines()
            lines.append(msg[-110:])
            self.log_label.configure(text="\n".join(lines[-2:]))
        except Exception:
            pass

    def _set_result_text(self, text: str):
        try:
            self.result_box.delete("1.0", "end")
            self.result_box.insert("1.0", text)
        except Exception:
            pass

    def _current_result(self) -> str:
        try:
            return self.result_box.get("1.0", "end").strip()
        except Exception:
            return ""

    def _copy_result(self):
        text = self._current_result()
        if not text:
            return
        try:
            import pyperclip
            pyperclip.copy(text)
            self._append_log("Copied to clipboard.")
        except Exception as exc:
            self._append_log(f"Copy failed: {exc}")

    def _paste_result(self):
        text = self._current_result()
        if not text:
            return
        if paste_text(text):
            self._append_log("Pasted into focused app.")
        else:
            self._append_log("Paste failed.")

    def _transcribe_file(self):
        try:
            from tkinter import filedialog
            path = filedialog.askopenfilename(
                title="Transcribe WAV file",
                filetypes=[("WAV audio", "*.wav"), ("All files", "*.*")])
        except Exception:
            return
        if not path:
            return
        with self._state_lock:
            if not self.engines.ready or self.busy or self.recording:
                self._append_log("Busy — wait for the current job to finish.")
                return
            self.busy = True
        self._set_status("Transcribing file…", self.T["yellow"])
        threading.Thread(target=self._file_worker, args=(path,),
                         daemon=True).start()

    def _file_worker(self, path: str):
        try:
            with wave.open(path, "rb") as src:
                ch, width, rate = (src.getnchannels(), src.getsampwidth(),
                                   src.getframerate())
                raw = src.readframes(src.getnframes())
            data = array.array({1: "B", 2: "h", 4: "i"}.get(width, "h"))
            try:
                data.frombytes(raw)
            except Exception:
                raise RuntimeError("Unsupported WAV format.")
            import numpy as np
            scale = 128.0 if width == 1 else float(1 << (8 * width - 1))
            vals = np.asarray(data, dtype=np.float32)
            if ch > 1:
                vals = vals.reshape(-1, ch).mean(axis=1).astype(np.float32)
            vals = vals / scale
            if rate != SAMPLE_RATE:
                vals = resample_to_16k(vals, rate)
            clean, _ = preprocess(vals)
            pro = self.engines.mode == "pro"
            result = self.engines.transcribe(
                clean, language=lang_or_none(self.cfg.get("language", "auto"),
                                             pro_engine=pro),
                hotwords=parse_hotwords(self.cfg.get("hotwords", "")),
                corrections=self.cfg.get("corrections", []))
            text = (result.get("text") or "").strip()
            text = apply_corrections(text, self.cfg.get("corrections", []),
                                     smart_dots=bool(
                                         self.cfg.get("smart_dots", True)))
            if self.cfg.get("remove_fillers", True):
                text = remove_fillers(text)
            if self.cfg.get("clean_output", True):
                text = clean_text(
                    text, trailing_space=bool(self.cfg.get("trailing_space", True)))
            self._call_in_ui(self._on_result, text, result)
        except Exception as exc:  # noqa: BLE001
            log.exception("file")
            self._call_in_ui(self._on_error, f"File error: {exc}")

    # -- history -------------------------------------------------------------------------------
    def _load_history(self) -> list[dict]:
        try:
            if os.path.exists(HISTORY_PATH):
                with open(HISTORY_PATH, "r", encoding="utf-8") as fh:
                    items = json.load(fh)
                return items if isinstance(items, list) else []
        except Exception:
            pass
        return []

    def _save_history(self):
        try:
            with open(HISTORY_PATH, "w", encoding="utf-8") as fh:
                json.dump(self.history[-100:], fh, ensure_ascii=False, indent=1)
        except Exception as exc:
            log.warning("save_history: %s", exc)

    def _push_history(self, text: str, lang: str):
        self.history.append({"t": time.strftime("%H:%M:%S"), "lang": lang,
                             "text": text})
        self.history = self.history[-100:]
        self._save_history()
        self._refresh_history()

    def _refresh_history(self):
        import customtkinter as ctk
        T = self.T
        try:
            for child in self.history_frame.winfo_children():
                child.destroy()
            items = list(reversed(self.history[-15:]))
            if not items:
                ctk.CTkLabel(self.history_frame, text="Nothing yet — hold "
                             "Right Ctrl and speak.", text_color=T["muted"],
                             font=F_BODY(11)).pack(anchor="w", padx=6, pady=4)
                return
            for _i, item in enumerate(items):
                preview = item.get("text", "").replace("\n", " ⏎ ")[:58]
                full = item.get("text", "")

                def _copy(t=full):
                    try:
                        import pyperclip
                        pyperclip.copy(t)
                        self._append_log("History item copied.")
                    except Exception as exc:
                        self._append_log(f"Copy failed: {exc}")

                ctk.CTkButton(self.history_frame,
                              text=f"{item.get('t', '')}  {preview}",
                              anchor="w",
                              fg_color=T["glow"] if _i == 0 else T["card2"],
                              hover_color=T["border"], corner_radius=8,
                              height=30, text_color=T["fg"],
                              font=F_BODY(11),
                              command=_copy).pack(fill="x", padx=2, pady=2)
        except Exception:
            pass

    def _export_history(self):
        try:
            from tkinter import filedialog
            path = filedialog.asksaveasfilename(
                title="Export history", defaultextension=".txt",
                filetypes=[("Text", "*.txt")])
            if not path:
                return
            with open(path, "w", encoding="utf-8") as fh:
                for item in self.history:
                    fh.write(f"[{item.get('t', '')}] {item.get('text', '')}\n")
            self._append_log(f"History exported ({len(self.history)} takes).")
        except Exception as exc:
            self._append_log(f"Export failed: {exc}")

    # -- hotwords manager --------------------------------------------------------------------------
    def _refresh_hw_chips(self):
        import customtkinter as ctk
        T = self.T
        try:
            for child in self.hw_chips.winfo_children():
                child.destroy()
            words = hotword_list(self.cfg.get("hotwords", ""))
            if not words:
                ctk.CTkLabel(self.hw_chips, text="none yet",
                             text_color=T["muted"],
                             font=F_BODY(10)).pack(side="left")
                return
            for w in words:
                ctk.CTkButton(self.hw_chips, text=f"{w}  ×", width=28,
                              height=24, fg_color=T["card2"],
                              hover_color=T["border"], text_color=T["fg"],
                              font=F_BODY(10),
                              command=lambda w=w: self._hw_remove(w)).pack(
                                  side="left", padx=(0, 6), pady=2)
        except Exception:
            pass

    def _hw_add(self):
        try:
            word = self.hw_entry.get().strip().strip(",")
        except Exception:
            return
        if not word:
            return
        words = hotword_list(self.cfg.get("hotwords", ""))
        if word not in words:
            words.append(word)
            self.cfg["hotwords"] = ", ".join(words)
            save_config(self.cfg)
            self._refresh_hw_chips()
            self._append_log(f"Hotword added: {word}.")
        try:
            self.hw_entry.delete(0, "end")
        except Exception:
            pass

    def _hw_remove(self, word: str):
        words = [w for w in hotword_list(self.cfg.get("hotwords", ""))
                 if w != word]
        self.cfg["hotwords"] = ", ".join(words)
        save_config(self.cfg)
        self._refresh_hw_chips()

    # -- taught words ------------------------------------------------------------
    def _taught_pairs(self) -> list:
        pairs = self.cfg.get("corrections", [])
        return [[str(w), str(r)] for w, r in pairs if str(w).strip()]

    def _refresh_taught_chips(self):
        import customtkinter as ctk
        T = self.T
        try:
            for child in self.taught_chips.winfo_children():
                child.destroy()
            pairs = self._taught_pairs()
            if not pairs:
                ctk.CTkLabel(self.taught_chips, text="none yet — teach one above",
                             text_color=T["muted"],
                             font=F_BODY(10)).pack(side="left")
                return
            for wrong, right in pairs[:12]:
                ctk.CTkButton(self.taught_chips, text=f"{wrong} → {right}  ×",
                              height=24, fg_color=T["card2"],
                              hover_color=T["border"], text_color=T["fg"],
                              font=F_BODY(10),
                              command=lambda w=wrong: self._taught_remove(w)).pack(
                                  side="left", padx=(0, 6), pady=2)
        except Exception:
            pass

    def _taught_add(self):
        try:
            hears = self.taught_hears.get().strip()
            writes = self.taught_writes.get().strip()
        except Exception:
            return
        if not hears or not writes:
            return
        pairs = self._taught_pairs()
        pairs = [[w, r] for w, r in pairs if w.lower() != hears.lower()]
        pairs.insert(0, [hears, writes])
        self.cfg["corrections"] = pairs[:40]
        save_config(self.cfg)
        self._refresh_taught_chips()
        self._append_log(f"Taught: “{hears}” → “{writes}”.")
        try:
            self.taught_hears.delete(0, "end")
            self.taught_writes.delete(0, "end")
        except Exception:
            pass

    def _taught_remove(self, hears: str):
        pairs = [[w, r] for w, r in self._taught_pairs()
                 if w.lower() != hears.lower()]
        self.cfg["corrections"] = pairs
        save_config(self.cfg)
        self._refresh_taught_chips()

    # -- settings --------------------------------------------------------------------------------------
    def _apply_paste_mode_visibility(self):
        try:
            if self.cfg.get("paste_mode") == "compose":
                self.compose_card.pack(fill="x", padx=16, pady=4)
            else:
                self.compose_card.pack_forget()
        except Exception:
            pass

    def _save_settings(self):
        prev_theme = self.cfg.get("theme", "golden")
        self.cfg["theme"] = self.var_theme.get()
        self.cfg["engine_mode"] = self.var_mode.get()
        self.cfg["paste_mode"] = self.var_paste.get()
        self.cfg["language"] = self.var_lang.get()
        self.cfg["mic"] = self.var_mic.get().strip() or "default"
        self.cfg["hotkey_hold"] = self.var_hold.get().strip().lower() or "right ctrl"
        self.cfg["hotkey_toggle"] = self.var_toggle.get().strip().lower() or "f9"
        self.cfg["hotkey_dashboard"] = self.var_dashkey.get().strip().lower() or "ctrl+alt+d"
        self.cfg["auto_paste"] = bool(self.var_autopaste.get())
        self.cfg["copy_to_clipboard"] = bool(self.var_clipboard.get())
        self.cfg["live_partials"] = bool(self.var_partials.get())
        self.cfg["voice_commands"] = bool(self.var_commands.get())
        self.cfg["sounds"] = bool(self.var_sounds.get())
        self.cfg["remove_fillers"] = bool(self.var_fillers.get())
        self.cfg["smart_dots"] = bool(self.var_dots.get())
        self.cfg["autostart"] = bool(self.var_autostart.get())
        self.cfg["start_minimized"] = bool(self.var_minimized.get())
        save_config(self.cfg)
        self.engines.mode = self.cfg["engine_mode"]
        if FROZEN:
            set_autostart(self.cfg["autostart"])
        elif self.cfg["autostart"]:
            self._append_log("Note: autostart applies to the installed "
                             ".exe (not dev mode).")
        self._apply_paste_mode_visibility()
        self._start_hotkeys()
        self._refresh_stats()
        self._append_log(f"Settings saved · engine: {self.engines.describe()}.")
        self._set_status_ready()
        if self.cfg["theme"] != prev_theme:
            try:
                from tkinter import messagebox
                if messagebox.askyesno(
                        APP_NAME,
                        f"Theme “{self.cfg['theme']}” needs a restart to apply. "
                        "Restart now?"):
                    self._restart_app()
            except Exception:
                pass

    def _restart_app(self):
        try:
            if self.tray is not None:
                self.tray.stop()
        except Exception:
            pass
        try:
            import keyboard
            keyboard.unhook_all()
        except Exception:
            pass
        try:
            if hasattr(self, "_single_sock") and self._single_sock:
                self._single_sock.close()
        except Exception:
            pass
        try:
            import subprocess
            if FROZEN:
                subprocess.Popen([sys.executable] + sys.argv[1:])
            else:
                subprocess.Popen([sys.executable, os.path.abspath(__file__)] +
                                 [a for a in sys.argv[1:]])
        except Exception as exc:
            log.error("restart: %s", exc)
            return
        os._exit(0)

    # -- per-frame tick --------------------------------------------------------------------------------------
    def _tick(self):
        self._drain_ui_queue()
        try:
            if self.recording:
                held = time.perf_counter() - self.rec_started
                self.timer_label.configure(
                    text=f"● recording {held:.1f}s — release "
                         f"{self.cfg.get('hotkey_hold')}")
                self._draw_wave()
                try:
                    blink = self.T["red"] if int(held * 3) % 2 == 0 else "#5a2626"
                    self.status_dot.configure(text_color=blink)
                except Exception:
                    pass
                if self.cfg.get("show_overlay", True):
                    self.overlay.set_status(f"● REC  {held:.1f}s",
                                            self.T["red"])
                    self.overlay.set_level(self.mic.level)
                self._draw_mic()
                if held >= MAX_SECONDS:
                    self.stop_and_transcribe()
            else:
                try:
                    if str(self.timer_label.cget("text")):
                        self.timer_label.configure(text="")
                    self._draw_wave()
                except Exception:
                    pass
            if int(time.perf_counter() - self._boot) % 30 == 0:
                self._refresh_stats()
        except Exception:
            pass
        self.root.after(50, self._tick)

    # -- minimize-to-tray / quit -------------------------------------------------------------------------------
    def _hide_to_tray(self):
        try:
            self.root.withdraw()
        except Exception:
            pass

    def _show_window(self):
        try:
            self.root.deiconify()
        except Exception:
            pass
        _force_foreground(self.root)

    def _quit(self):
        log.info("quit requested")
        try:
            if self.tray is not None:
                self.tray.stop()
        except Exception:
            pass
        self._destroy()

    def _destroy(self):
        try:
            if self.recording:
                self.mic.abort()
        except Exception:
            pass
        try:
            self.overlay.hide()
        except Exception:
            pass
        try:
            import keyboard
            keyboard.unhook_all()
        except Exception:
            pass
        try:
            if hasattr(self, "_single_sock") and self._single_sock:
                self._single_sock.close()
        except Exception:
            pass
        try:
            self.root.destroy()
        except Exception:
            pass

    def _on_close(self):
        # X minimizes to tray (24/7 hotkeys); Quit via tray exits.
        if self.tray is not None:
            self._hide_to_tray()
            return
        self._destroy()


def make_tray_image_fallback(size: int = 64):
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle([2, 2, size - 3, size - 3], radius=size // 4,
                           fill=(227, 183, 107, 255))
    draw.text((size / 2, size / 2 - 1), "D", fill=(20, 15, 13), anchor="mm")
    return img


def check_deps() -> list[str]:
    missing = []
    for mod, pip_name in (("numpy", "numpy"),
                          ("sounddevice", "sounddevice"),
                          ("soxr", "soxr"),
                          ("keyboard", "keyboard"),
                          ("pyperclip", "pyperclip"),
                          ("customtkinter", "customtkinter"),
                          ("pystray", "pystray"),
                          ("PIL", "pillow"),
                          ("faster_whisper", "faster-whisper")):
        try:
            __import__(mod)
        except ImportError:
            missing.append(pip_name)
    try:
        import needle  # noqa: F401
    except ImportError:
        missing.append("cactus-needle[mic]")
    return sorted(set(missing))


def _force_foreground(tkwin=None):
    """Aggressively bring a window to the front (tray clicks, 2nd launch)."""
    try:
        hwnd = None
        if tkwin is not None:
            try:
                tkwin.update_idletasks()
                hwnd = tkwin.winfo_id()
            except Exception:
                hwnd = None
        if hwnd:
            u = ctypes.windll.user32
            u.ShowWindow(hwnd, 9)
            u.BringWindowToTop(hwnd)
            u.SetForegroundWindow(hwnd)
            return True
    except Exception:
        pass
    return False


def _foreground_existing():
    """Find an already-running Draft dashboard and pull it forward."""
    found = []
    try:
        u = ctypes.windll.user32

        @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        def _cb(hwnd, _):
            try:
                if u.IsWindowVisible(hwnd):
                    ln = u.GetWindowTextLengthW(hwnd)
                    buf = ctypes.create_unicode_buffer(ln + 1)
                    u.GetWindowTextW(hwnd, buf, ln + 1)
                    if "Draft" in buf.value and "local speech" in buf.value:
                        found.append(hwnd)
            except Exception:
                pass
            return True

        u.EnumWindows(_cb, 0)
    except Exception:
        pass
    for hwnd in found:
        try:
            u = ctypes.windll.user32
            u.ShowWindow(hwnd, 9)
            u.BringWindowToTop(hwnd)
            u.SetForegroundWindow(hwnd)
            return True
        except Exception:
            pass
    return False


def _beat(stage: str) -> None:
    try:
        with open(os.path.join(DATA_DIR, "boot.txt"), "a",
                  encoding="utf-8") as fh:
            fh.write(f"{time.time():.1f} {stage}\n")
    except Exception:
        pass


def _splash_text(text: str) -> None:
    try:
        import pyi_splash  # type: ignore
        pyi_splash.update_text(text)
    except Exception:
        pass


try:
    import faulthandler as _fh
    _fh.dump_traceback_later(
        90, file=open(os.path.join(DATA_DIR, "crash.txt"), "w",
                      encoding="utf-8"))
except Exception:
    pass


def _ensure_single_instance():
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", _SINGLE_PORT))
        return s
    except OSError:
        return None


def _try_patch() -> bool:
    """Run a loose patch/draft.py next to the exe instead of frozen code.

    Lets fixes ship as one small file (patch_app.bat, seconds) instead of
    full rebuilds. Returns True when the patch took over.
    """
    if not getattr(sys, "frozen", False) or os.environ.get("DRAFT_PATCHED"):
        return False
    try:
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
        cand = os.path.join(exe_dir, "patch", "draft.py")
        if not os.path.exists(cand):
            return False
        os.environ["DRAFT_PATCHED"] = "1"
        import runpy
        log.info("patch bootstrap: running %s", cand)
        runpy.run_path(cand, run_name="__main__")
        return True
    except SystemExit:
        raise
    except Exception:
        log.exception("patch bootstrap failed, using frozen code")
        return False


def main():
    _beat("main-enter")
    if _try_patch():
        return
    parser = argparse.ArgumentParser(prog=APP_NAME)
    parser.add_argument("--minimized", action="store_true",
                        help="start hidden in the system tray")
    args = parser.parse_args()

    sock = _ensure_single_instance()
    if sock is None:
        try:
            import pyi_splash  # type: ignore
            pyi_splash.close()
        except Exception:
            pass
        _foreground_existing()
        try:
            ctypes.windll.user32.MessageBoxW(
                None, "Draft is already running — its window is now in front.",
                "Draft", 0x40)
        except Exception:
            pass
        sys.exit(0)

    import customtkinter as ctk
    _beat("imports-done")
    _splash_text("Opening Draft…")
    missing = check_deps()
    if missing:
        print(f"{APP_NAME}: missing packages: {', '.join(missing)}")
        print("Run install.bat (or: py -3.13 -m pip install -r requirements.txt)")
        try:
            import tkinter as tk
            from tkinter import messagebox
            root = tk.Tk()
            root.withdraw()
            messagebox.showerror(
                APP_NAME,
                "Missing packages:\n" + "\n".join(missing) +
                "\n\nRun install.bat first.")
        except Exception:
            pass
        sys.exit(1)
    ctk.set_appearance_mode("dark")
    root = ctk.CTk()
    _beat("root-created")
    app = DraftApp(root, start_minimized=args.minimized)
    app._single_sock = sock
    _beat("app-built")
    root.mainloop()


if __name__ == "__main__":
    main()
