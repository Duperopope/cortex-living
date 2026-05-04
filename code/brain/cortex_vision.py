"""
cortex_vision.py — Capacité visuelle de Cortex.

Capture d'écran + envoi à un modèle vision pour décrire ce que Sam voit.
Utilise PIL/mss pour capture, et envoie l'image en base64 à un endpoint vision.

Modèles vision compatibles :
- Local : LM Studio avec un modèle vision (qwen-vl, llava)
- Sinon : description basique via OCR si tesseract dispo

L'API est volontairement minimale : screenshot + describe.
"""
import base64
import io
import json
import sys
import time
import urllib.request
from pathlib import Path

LM_STUDIO_URL = "http://localhost:1234/v1/chat/completions"


def capture_screen() -> dict:
    """Capture l'écran principal et retourne PNG bytes + path tmp."""
    try:
        import mss
        with mss.mss() as sct:
            mon = sct.monitors[1]  # écran principal
            img = sct.grab(mon)
            from PIL import Image
            pil = Image.frombytes("RGB", img.size, img.bgra, "raw", "BGRX")
            # Réduire taille pour LLM (max 1280px largeur)
            if pil.width > 1280:
                ratio = 1280 / pil.width
                pil = pil.resize((1280, int(pil.height * ratio)), Image.LANCZOS)
            buf = io.BytesIO()
            pil.save(buf, format="PNG", optimize=True)
            png_bytes = buf.getvalue()
            tmp_path = Path.home() / ".cortex_screenshot.png"
            tmp_path.write_bytes(png_bytes)
            return {"ok": True, "path": str(tmp_path),
                    "size_kb": round(len(png_bytes) / 1024, 1),
                    "dimensions": pil.size, "bytes_b64": base64.b64encode(png_bytes).decode()}
    except ImportError:
        return {"ok": False, "error": "mss/PIL not installed (pip install mss pillow)"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _try_lm_vision(b64_png: str, prompt: str, model: str = None) -> str | None:
    """Tentative LM Studio vision (qwen-vl, llava). Retourne None si pas dispo."""
    payload = {
        "model": model or "qwen2-vl",  # auto-fallback géré côté serveur
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url",
                 "image_url": {"url": f"data:image/png;base64,{b64_png}"}}
            ]
        }],
        "max_tokens": 400, "temperature": 0.3, "stream": False,
    }
    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(LM_STUDIO_URL, data=data,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as r:
            d = json.loads(r.read().decode())
            return d["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"[vision] LM Studio err: {e}", flush=True)
        return None


def _try_ocr(png_path: str) -> str | None:
    """Fallback OCR via tesseract si dispo."""
    try:
        import pytesseract
        from PIL import Image
        text = pytesseract.image_to_string(Image.open(png_path), lang="fra+eng")
        return text.strip()[:1500] if text else None
    except Exception:
        return None


def _basic_scene_analysis(png_path: str) -> str | None:
    """Fallback ultime : analyse basique cv2 (luminosité, visages, couleur dominante)."""
    try:
        import cv2, numpy as np
        img = cv2.imread(png_path)
        if img is None: return None
        h, w = img.shape[:2]
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        brightness = float(gray.mean())
        contrast = float(gray.std())
        # Dominant color (mean BGR)
        b, g, r = [float(c) for c in cv2.mean(img)[:3]]
        bgr_label = "rouge" if r > b and r > g else ("vert" if g > b else "bleu")
        # Face detection (Haar cascade)
        try:
            face_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_frontalface_default.xml')
            faces = face_cascade.detectMultiScale(gray, scaleFactor=1.2, minNeighbors=5)
            n_faces = len(faces)
        except Exception:
            n_faces = 0
        # Edge density (proxy for "complexity")
        edges = cv2.Canny(gray, 100, 200)
        edge_ratio = float(edges.sum()) / (255 * h * w)
        scene_type = ("très sombre" if brightness < 30 else
                      "sombre" if brightness < 80 else
                      "moyennement éclairé" if brightness < 150 else
                      "lumineux")
        complexity = ("simple/uniforme" if edge_ratio < 0.02 else
                      "modérément détaillé" if edge_ratio < 0.08 else
                      "très détaillé/complexe")
        parts = [
            f"Image {w}×{h}, {scene_type} (luminosité {brightness:.0f}/255), "
            f"contraste {contrast:.0f}, dominante {bgr_label}.",
            f"Composition : {complexity} (densité d'arêtes {edge_ratio:.2%}).",
        ]
        if n_faces > 0:
            parts.append(f"Détection : {n_faces} visage(s) humain(s).")
        return " ".join(parts)
    except Exception as e:
        return f"(analyse cv2 err: {e})"


def _find_best_camera(max_idx: int = 8) -> tuple[int, "any"] | None:
    """Scanne toutes les caméras avec DirectShow ET Media Foundation (capture
    virtual cams type Phone Link/Samsung DeX). Score = résolution × qualité."""
    import cv2, numpy as np
    candidates = []
    # Tester les deux backends — souvent les virtual cams n'apparaissent qu'en MSMF
    backends = [(cv2.CAP_DSHOW, "DSHOW"), (cv2.CAP_MSMF, "MSMF")]
    seen_signatures = set()
    for backend_id, backend_name in backends:
        for idx in range(max_idx):
            try:
                cap = cv2.VideoCapture(idx, backend_id)
                if not cap.isOpened(): continue
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, 3840)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 2160)
                for _ in range(5): cap.read()
                ret, frame = cap.read()
                cap.release()
                if not ret or frame is None: continue
                h, w = frame.shape[:2]
                pixels = w * h
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                mean = float(gray.mean())
                std  = float(gray.std())
                # Dédup signatures (idx + backend + résolution + brightness)
                sig = (idx, backend_name, w, h, round(mean / 5))
                if sig in seen_signatures: continue
                seen_signatures.add(sig)
                edges = cv2.Canny(gray, 50, 150)
                edge_ratio = float(edges.sum()) / (255 * h * w)
                if mean < 8:
                    score = 0
                else:
                    res_bonus = (pixels / 1_000_000) * 30
                    content = std * 1.0 + edge_ratio * 1000 + min(mean, 200) * 0.1
                    score = content + res_bonus
                candidates.append((idx, backend_name, frame, score, mean, std, w, h))
            except Exception: continue
    if not candidates: return None
    candidates.sort(key=lambda x: -x[3])
    best = candidates[0]
    print(f"[vision] cameras: " + " | ".join(
        f"{c[1]}:idx={c[0]} {c[6]}x{c[7]} score={c[3]:.1f}" for c in candidates[:8]), flush=True)
    return best[0], best[2]

_camera_state = {"brightness": None, "contrast": None, "exposure": None, "saturation": None}

VISION_MUTED_FLAG = Path.home() / ".claude" / "projects" / "h--Code-Paperclip" / "memory" / ".cortex-vision-muted.flag"

def is_vision_muted() -> bool:
    return VISION_MUTED_FLAG.exists()

def set_vision_muted(muted: bool) -> dict:
    if muted:
        VISION_MUTED_FLAG.parent.mkdir(parents=True, exist_ok=True)
        VISION_MUTED_FLAG.touch()
        # Couper la capture continue
        try: stop_continuous_capture()
        except: pass
    else:
        try: VISION_MUTED_FLAG.unlink()
        except: pass
    return {"ok": True, "muted": is_vision_muted()}

def auto_tune_from_frame(frame) -> dict:
    """Cortex ajuste ses propres params si l'image est mal exposée.
    Retourne les ajustements appliqués."""
    import cv2
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    mean = float(gray.mean())
    adjustments = {}
    # Trop sombre → augmenter brightness/exposure
    if mean < 60:
        new_bri = min(255, (_camera_state.get("brightness") or 128) + 20)
        new_exp = min(0, (_camera_state.get("exposure") or -6) + 1)
        adjustments["brightness"] = new_bri
        adjustments["exposure"] = new_exp
    # Trop clair → baisser
    elif mean > 200:
        new_bri = max(0, (_camera_state.get("brightness") or 128) - 20)
        new_exp = max(-13, (_camera_state.get("exposure") or -6) - 1)
        adjustments["brightness"] = new_bri
        adjustments["exposure"] = new_exp
    if adjustments:
        set_camera_params(**adjustments)
    return {"mean": mean, "adjustments": adjustments}

def set_camera_params(brightness: float | None = None, contrast: float | None = None,
                      exposure: float | None = None, saturation: float | None = None) -> dict:
    """Met à jour les params caméra appliqués à chaque capture."""
    if brightness is not None: _camera_state["brightness"] = brightness
    if contrast   is not None: _camera_state["contrast"]   = contrast
    if exposure   is not None: _camera_state["exposure"]   = exposure
    if saturation is not None: _camera_state["saturation"] = saturation
    return {"ok": True, **_camera_state}

_cached_best_camera = None
import threading as _th
_capture_lock = _th.Lock()  # évite les conflits cv2 sur appels parallèles

def reset_camera_cache():
    global _cached_best_camera
    _cached_best_camera = None
    return {"ok": True}

_last_frame_path = None  # cache du dernier path écrit

# ─── Thread de capture continue (caméra reste ouverte) ───────────────────────
_continuous_state = {"thread": None, "running": False, "last_png_bytes": None,
                     "last_ts": 0, "fps_target": 5}

def _continuous_capture_loop():
    """Boucle qui maintient la caméra ouverte et capture en continu."""
    import cv2, io
    from PIL import Image
    cam_idx = _cached_best_camera
    if cam_idx is None:
        # Auto-détection au démarrage du thread
        with _capture_lock:
            picked = _find_best_camera()
            if picked is None:
                _continuous_state["running"] = False
                return
            cam_idx = picked[0]
            globals()["_cached_best_camera"] = cam_idx
    cap = cv2.VideoCapture(cam_idx, cv2.CAP_DSHOW)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
    if not cap.isOpened():
        _continuous_state["running"] = False
        return
    print(f"[vision] continuous capture started (cam={cam_idx})", flush=True)
    while _continuous_state["running"]:
        # Respect du flag vision-muted : si muté, on libère la cam et on attend.
        # C'est le drapeau qui pilote la LED côté physique.
        if is_vision_muted():
            try: cap.release()
            except Exception: pass
            print("[vision] muted → camera released, waiting for unmute", flush=True)
            while _continuous_state["running"] and is_vision_muted():
                time.sleep(2.0)
            if not _continuous_state["running"]: return
            # Réouvre la cam après unmute
            cap = cv2.VideoCapture(cam_idx, cv2.CAP_DSHOW)
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
            if not cap.isOpened():
                print("[vision] failed to reopen after unmute", flush=True)
                _continuous_state["running"] = False
                return
            print("[vision] camera reopened after unmute", flush=True)
        try:
            # Appliquer params si changés
            if _camera_state["brightness"] is not None: cap.set(cv2.CAP_PROP_BRIGHTNESS, _camera_state["brightness"])
            if _camera_state["contrast"]   is not None: cap.set(cv2.CAP_PROP_CONTRAST,   _camera_state["contrast"])
            if _camera_state["exposure"]   is not None: cap.set(cv2.CAP_PROP_EXPOSURE,   _camera_state["exposure"])
            if _camera_state["saturation"] is not None: cap.set(cv2.CAP_PROP_SATURATION, _camera_state["saturation"])
            ret, frame = cap.read()
            if ret and frame is not None and float(frame.mean()) > 5:
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                pil = Image.fromarray(rgb)
                buf = io.BytesIO()
                pil.save(buf, format="PNG", optimize=False, compress_level=1)  # compress fast
                _continuous_state["last_png_bytes"] = buf.getvalue()
                _continuous_state["last_ts"] = time.time()
        except Exception as e:
            print(f"[vision continuous] {e}", flush=True)
        time.sleep(1.0 / max(1, _continuous_state["fps_target"]))
    cap.release()
    print("[vision] continuous capture stopped", flush=True)


def start_continuous_capture(fps: int = 5):
    if _continuous_state["running"]: return {"ok": True, "already_running": True}
    _continuous_state["fps_target"] = fps
    _continuous_state["running"] = True
    t = _th.Thread(target=_continuous_capture_loop, daemon=True)
    _continuous_state["thread"] = t
    t.start()
    return {"ok": True}


def stop_continuous_capture():
    _continuous_state["running"] = False
    return {"ok": True}


def get_latest_frame_bytes() -> bytes | None:
    """Retourne le PNG du dernier frame capturé en continu (None si pas de capture)."""
    return _continuous_state.get("last_png_bytes")

def capture_webcam(camera_idx: int | None = None) -> dict:
    """Capture une frame webcam."""
    global _last_frame_path, _cached_best_camera
    if not _capture_lock.acquire(timeout=2.0):
        if _last_frame_path and Path(_last_frame_path).exists():
            return {"ok": True, "path": _last_frame_path, "cached": True,
                    "camera_idx": _cached_best_camera}
        return {"ok": False, "error": "capture busy"}
    try:
        import cv2
        from PIL import Image
        if camera_idx is None:
            if _cached_best_camera is None:
                picked = _find_best_camera()
                if picked is None:
                    return {"ok": False, "error": "aucune caméra avec contenu détectable"}
                _cached_best_camera = picked[0]
                camera_idx, frame = picked
            else:
                # Utiliser la caméra mise en cache (rapide)
                cap = cv2.VideoCapture(_cached_best_camera, cv2.CAP_DSHOW)
                if not cap.isOpened():
                    _cached_best_camera = None  # invalidate cache
                    return capture_webcam(None)  # re-scan
                # Forcer haute résolution (la caméra clampe à son max)
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, 3840)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 2160)
                if _camera_state["brightness"] is not None: cap.set(cv2.CAP_PROP_BRIGHTNESS, _camera_state["brightness"])
                if _camera_state["contrast"]   is not None: cap.set(cv2.CAP_PROP_CONTRAST,   _camera_state["contrast"])
                if _camera_state["exposure"]   is not None: cap.set(cv2.CAP_PROP_EXPOSURE,   _camera_state["exposure"])
                if _camera_state["saturation"] is not None: cap.set(cv2.CAP_PROP_SATURATION, _camera_state["saturation"])
                for _ in range(3): cap.read()
                ret, frame = cap.read()
                cap.release()
                if not ret:
                    _cached_best_camera = None
                    return {"ok": False, "error": "frame read failed"}
                # Si le frame est tout noir, invalide le cache et rescan
                if frame is not None and float(frame.mean()) < 5:
                    _cached_best_camera = None
                    _capture_lock.release()
                    return capture_webcam(None)
                camera_idx = _cached_best_camera
        else:
            cap = cv2.VideoCapture(camera_idx, cv2.CAP_DSHOW)
            if not cap.isOpened():
                return {"ok": False, "error": f"webcam {camera_idx} unavailable"}
            # Apply params si configurés
            if _camera_state["brightness"] is not None: cap.set(cv2.CAP_PROP_BRIGHTNESS, _camera_state["brightness"])
            if _camera_state["contrast"]   is not None: cap.set(cv2.CAP_PROP_CONTRAST,   _camera_state["contrast"])
            if _camera_state["exposure"]   is not None: cap.set(cv2.CAP_PROP_EXPOSURE,   _camera_state["exposure"])
            if _camera_state["saturation"] is not None: cap.set(cv2.CAP_PROP_SATURATION, _camera_state["saturation"])
            for _ in range(3): cap.read()
            ret, frame = cap.read()
            cap.release()
            if not ret:
                return {"ok": False, "error": "frame read failed"}
        # BGR → RGB
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        pil = Image.fromarray(rgb)
        if pil.width > 1280:
            ratio = 1280 / pil.width
            pil = pil.resize((1280, int(pil.height * ratio)), Image.LANCZOS)
        buf = io.BytesIO()
        pil.save(buf, format="PNG", optimize=True)
        png_bytes = buf.getvalue()
        tmp_path = Path.home() / ".cortex_webcam.png"
        tmp_path.write_bytes(png_bytes)
        _last_frame_path = str(tmp_path)
        return {"ok": True, "path": str(tmp_path),
                "size_kb": round(len(png_bytes) / 1024, 1),
                "camera_idx": camera_idx,
                "dimensions": pil.size, "bytes_b64": base64.b64encode(png_bytes).decode()}
    except ImportError:
        return {"ok": False, "error": "opencv not installed (pip install opencv-python)"}
    except Exception as e:
        return {"ok": False, "error": str(e)}
    finally:
        try: _capture_lock.release()
        except: pass


def see(prompt: str | None = None, source: str = "screen") -> dict:
    """Capture + describe. source: 'screen' (default) ou 'webcam'."""
    if source == "webcam":
        cap = capture_webcam()
    else:
        cap = capture_screen()
    if not cap.get("ok"): return cap

    user_prompt = prompt or (
        f"Décris brièvement ce que tu vois sur cette {'image webcam' if source == 'webcam' else 'capture écran'}. "
        f"3-4 phrases en français max."
    )

    # 1. Tenter LM Studio vision
    desc = _try_lm_vision(cap["bytes_b64"], user_prompt)
    if desc:
        return {"ok": True, "description": desc, "method": "lm_studio_vision",
                "screenshot": cap["path"], "size_kb": cap["size_kb"]}

    # 2. Fallback OCR
    text = _try_ocr(cap["path"])
    if text:
        return {"ok": True, "description": f"(OCR fallback)\n{text}",
                "method": "tesseract_ocr", "screenshot": cap["path"]}

    # 3. Fallback ultime : analyse cv2 basique
    scene = _basic_scene_analysis(cap["path"])
    if scene:
        return {"ok": True, "description": scene + "\n\n(Pas de modèle vision dans LM Studio. "
                "Charge un modèle vision type qwen2-vl-7b ou llava pour avoir une vraie description.)",
                "method": "cv2_basic", "screenshot": cap["path"]}

    return {"ok": False, "error": "no vision model, no OCR, cv2 failed",
            "screenshot": cap["path"]}


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if len(sys.argv) > 1 and sys.argv[1] == "capture":
        r = capture_screen()
        r.pop("bytes_b64", None)
        print(json.dumps(r, ensure_ascii=False, indent=2))
    else:
        prompt = sys.argv[1] if len(sys.argv) > 1 else None
        r = see(prompt)
        print(json.dumps(r, ensure_ascii=False, indent=2)[:2000])
