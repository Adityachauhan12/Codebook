
"""
Performs liveliness detection.

- IndigoGroomingAssessment: original "from video" logic (unchanged).
- StreamingLiveliness: robust, stateful per-session detector for HTTP frame streaming.
"""

import os
from typing import Any, Dict, Optional
import base64
import cv2
import mediapipe as mp
import numpy as np
from dataclasses import dataclass, field
from datetime import datetime


# -------------------- Original class (unchanged) --------------------
class IndigoGroomingAssessment:
    """A class to handle liveliness detection using facial landmarks."""
    def __init__(self) -> None:
        self.mp_face_mesh = mp.solutions.face_mesh

    def detect_liveliness_and_capture_frame(self, video_path: str) -> Dict[str, Any]:
        cap = cv2.VideoCapture(video_path)
        blink_count = 0
        captured_frame_path = None
        ear_threshold = 0.25
        consecutive_frames = 0
        frame_count = 0
        recording_dir = os.path.join(os.getcwd(), "recording")
        os.makedirs(recording_dir, exist_ok=True)

        def _ear(eye_points: list) -> float:
            p2 = np.array([eye_points[1].x, eye_points[1].y])
            p6 = np.array([eye_points[5].x, eye_points[5].y])
            p3 = np.array([eye_points[2].x, eye_points[2].y])
            p5 = np.array([eye_points[4].x, eye_points[4].y])
            p1 = np.array([eye_points[0].x, eye_points[0].y])
            p4 = np.array([eye_points[3].x, eye_points[3].y])
            ver1 = np.linalg.norm(p2 - p6)
            ver2 = np.linalg.norm(p3 - p5)
            hor  = np.linalg.norm(p1 - p4)
            return (ver1 + ver2) / (2.0 * hor)

        face_mesh_args = {"static_image_mode": False, "max_num_faces": 1, "refine_landmarks": True}

        with mp.solutions.face_mesh.FaceMesh(**face_mesh_args) as face_mesh:
            while cap.isOpened():
                ret, frame = cap.read()
                if not ret:
                    break
                frame_count += 1
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                results = face_mesh.process(rgb)
                if results.multi_face_landmarks:
                    lm = results.multi_face_landmarks[0]
                    L = [lm.landmark[i] for i in [33,160,158,133,153,144]]
                    R = [lm.landmark[i] for i in [362,385,387,263,373,380]]
                    avg_ear = (_ear(L) + _ear(R)) / 2.0

                    if avg_ear < ear_threshold:
                        consecutive_frames += 1
                    else:
                        if consecutive_frames >= 2:
                            blink_count += 1
                            if captured_frame_path is None:
                                captured_frame_path = os.path.join(recording_dir, f"blink_frame_{frame_count}.jpg")
                                cv2.imwrite(captured_frame_path, frame)
                        consecutive_frames = 0

        cap.release()
        cv2.destroyAllWindows()
        head_turned = True  # placeholder as before
        status = "LIVE" if (blink_count > 0 and head_turned) else "SPOOF"
        score  = 1.0 if status == "LIVE" else 0.0
        return {
            "liveliness_status": status,
            "blink_count": blink_count,
            "liveliness_score": score,
            "captured_frame_path": captured_frame_path,
            "video_path": video_path,
        }


# -------------------- Helpers shared by streaming --------------------
def decode_frame_b64(frame_b64: str):
    """Decodes base64 JPEG to BGR numpy image; returns None if decode fails."""
    try:
        data = base64.b64decode(frame_b64)
        arr = np.frombuffer(data, np.uint8)
        frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        return frame
    except Exception:
        return None

def _ear_points(eye_points: list) -> float:
    p2 = np.array([eye_points[1].x, eye_points[1].y])
    p6 = np.array([eye_points[5].x, eye_points[5].y])
    p3 = np.array([eye_points[2].x, eye_points[2].y])
    p5 = np.array([eye_points[4].x, eye_points[4].y])
    p1 = np.array([eye_points[0].x, eye_points[0].y])
    p4 = np.array([eye_points[3].x, eye_points[3].y])
    ver1 = np.linalg.norm(p2 - p6)
    ver2 = np.linalg.norm(p3 - p5)
    hor  = np.linalg.norm(p1 - p4)
    return (ver1 + ver2) / (2.0 * hor)


@dataclass
class SessionState:
    crew_name: str = "Unknown"
    iga_code: str  = "Unknown"
    baseline_ear: Optional[float] = None
    last_ear: float = 0.0
    in_blink: bool = False          # currently in a closed-eye state
    closed_frames: int = 0
    blink_count: int = 0
    baseline_nose_x: Optional[float] = None
    head_turned: bool = False
    created_at: datetime = field(default_factory=datetime.now)


class StreamingLiveliness:
    """
    Robust per-session detector for low-FPS HTTP streaming (3–6 fps).
    - Adaptive blink detection: EAR drop relative to baseline EAR (EMA).
    - Simple head-turn check: nose-tip X deviation vs baseline.
    """
    LEFT_EYE = [33,160,158,133,153,144]
    RIGHT_EYE = [362,385,387,263,373,380]
    NOSE_TIP_IDX = 1

    def __init__(
        self,
        require_head_turn: bool = True,
        ear_drop_ratio: float = 0.28,      # 28% drop from baseline counts as closed
        ema_alpha: float = 0.15,           # EMA update when eyes are open
        min_closed_frames: int = 1,        # works well for ~3–5 fps
        nose_dx_threshold: float = 0.02    # slightly more sensitive than before
    ):
        self.require_head_turn = require_head_turn
        self.ear_drop_ratio = ear_drop_ratio
        self.ema_alpha = ema_alpha
        self.min_closed_frames = min_closed_frames
        self.nose_dx_threshold = nose_dx_threshold

        self._sessions: Dict[str, SessionState] = {}
        self._face_mesh = mp.solutions.face_mesh.FaceMesh(
            static_image_mode=False, max_num_faces=1, refine_landmarks=True
        )

    def _get_state(self, session_id: str, crew_name: str, iga_code: str) -> SessionState:
        st = self._sessions.get(session_id)
        if st is None:
            st = SessionState(crew_name=crew_name, iga_code=iga_code)
            self._sessions[session_id] = st
        return st

    def end_session(self, session_id: str):
        self._sessions.pop(session_id, None)

    def process_frame(self, session_id: str, frame_bgr: np.ndarray, crew_name: str, iga_code: str) -> Dict[str, Any]:
        st = self._get_state(session_id, crew_name, iga_code)

        # Face landmarks
        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        results = self._face_mesh.process(rgb)
        if not results.multi_face_landmarks:
            return {"event": "progress", "blink_count": st.blink_count, "ear": st.last_ear, "baseline_ear": st.baseline_ear, "nose_dx": 0.0}

        lm = results.multi_face_landmarks[0]
        L = [lm.landmark[i] for i in self.LEFT_EYE]
        R = [lm.landmark[i] for i in self.RIGHT_EYE]
        ear_now = (_ear_points(L) + _ear_points(R)) / 2.0
        st.last_ear = float(ear_now)

        # Init baseline EAR with first few open-eye frames
        if st.baseline_ear is None:
            st.baseline_ear = float(ear_now)

        # Head-turn (nose x deviation)
        nose_x = lm.landmark[self.NOSE_TIP_IDX].x
        if st.baseline_nose_x is None:
            st.baseline_nose_x = float(nose_x)
        nose_dx = abs(nose_x - st.baseline_nose_x)
        if nose_dx > self.nose_dx_threshold:
            st.head_turned = True

        # Determine closed vs open using adaptive threshold
        closed = ear_now < (st.baseline_ear * (1.0 - self.ear_drop_ratio))

        if closed:
            st.closed_frames += 1
            st.in_blink = True
        else:
            # Update baseline EAR with EMA when eyes are open -> tracks lighting/pose changes
            st.baseline_ear = (1.0 - self.ema_alpha) * st.baseline_ear + self.ema_alpha * ear_now
            if st.in_blink:
                # blink ends; count it if we had enough closed frames
                if st.closed_frames >= self.min_closed_frames:
                    st.blink_count += 1
                st.in_blink = False
                st.closed_frames = 0

        # Success condition
        success = (st.blink_count > 0) and (st.head_turned or not self.require_head_turn)
        if success:
            ok, enc = cv2.imencode(".jpg", frame_bgr)
            if ok:
                img_bytes = enc.tobytes()
                return {
                    "event": "success",
                    "blink_count": st.blink_count,
                    "captured_frame_bytes": img_bytes,
                }
            return {"event": "error", "message": "Failed to encode success frame."}

        return {
            "event": "progress",
            "blink_count": st.blink_count,
            "ear": round(float(ear_now), 4),
            "baseline_ear": round(float(st.baseline_ear or 0.0), 4),
            "nose_dx": round(float(nose_dx), 4),
        }
