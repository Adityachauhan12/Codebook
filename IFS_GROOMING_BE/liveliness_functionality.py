"""
Performs liveliness detection.

- IndigoGroomingAssessment: original "from video" logic (unchanged).
- StreamingLiveliness: robust, stateful per-session detector for HTTP frame streaming.
"""

import os
from typing import Any, Dict, Optional, List
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
    """Holds the state for a single liveliness check session."""
    iga_code: str  = "Unknown"
    
    # Baseline calculation
    initial_readings: list = field(default_factory=list)
    baseline_ear: Optional[float] = None
    baseline_nose_x: Optional[float] = None

    # State for blink detection
    in_blink: bool = False
    closed_frames: int = 0
    
    # Flags to remember completed actions
    has_blinked: bool = False
    has_turned_left: bool = False
    has_turned_right: bool = False
    
    created_at: datetime = field(default_factory=datetime.now)


class StreamingLiveliness:
    """
    More robust and user-friendly liveliness detector.
    - Uses a stable baseline from the first few frames.
    - Remembers completed actions (blink, left, right turn).
    - Provides clear feedback on which actions are still required.
    """
    LEFT_EYE = [33, 160, 158, 133, 153, 144]
    RIGHT_EYE = [362, 385, 387, 263, 373, 380]
    NOSE_TIP_IDX = 1

    def __init__(
        self,
        ear_drop_ratio: float = 0.25,      # **FIX**: More sensitive blink threshold (25% drop)
        min_closed_frames: int = 1,        # 1 frame is sufficient for a blink at low FPS
        nose_dx_threshold: float = 0.028,  # Slightly relaxed head turn threshold
        baseline_frames: int = 5           # Use 5 frames to establish a stable baseline
    ):
        self.ear_drop_ratio = ear_drop_ratio
        self.min_closed_frames = min_closed_frames
        self.nose_dx_threshold = nose_dx_threshold
        self.baseline_frames = baseline_frames

        self._sessions: Dict[str, SessionState] = {}
        self._face_mesh = mp.solutions.face_mesh.FaceMesh(
            static_image_mode=False, max_num_faces=1, refine_landmarks=True
        )

    def _get_state(self, session_id: str, iga_code: str) -> SessionState:
        if session_id not in self._sessions:
            self._sessions[session_id] = SessionState(iga_code=iga_code)
        return self._sessions[session_id]

    def end_session(self, session_id: str):
        self._sessions.pop(session_id, None)

    def process_frame(self, session_id: str, frame_bgr: np.ndarray, iga_code: str, **kwargs) -> Dict[str, Any]:
        st = self._get_state(session_id, iga_code)

        # Helper to create a progress report
        def progress_report():
            return {
                "event": "progress",
                "actions_done": {
                    "blink": st.has_blinked,
                    "left": st.has_turned_left,
                    "right": st.has_turned_right,
                },
                "message": "Initializing..." if st.baseline_ear is None else "Perform actions"
            }

        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        results = self._face_mesh.process(rgb)
        if not results.multi_face_landmarks:
            return progress_report()

        lm = results.multi_face_landmarks[0]
        ear_now = (_ear_points([lm.landmark[i] for i in self.LEFT_EYE]) + 
                   _ear_points([lm.landmark[i] for i in self.RIGHT_EYE])) / 2.0
        nose_x = lm.landmark[self.NOSE_TIP_IDX].x

        # --- 1. BASELINE CALCULATION ---
        if st.baseline_ear is None:
            st.initial_readings.append({'ear': ear_now, 'nose_x': nose_x})
            if len(st.initial_readings) >= self.baseline_frames:
                st.baseline_ear = sum(r['ear'] for r in st.initial_readings) / len(st.initial_readings)
                st.baseline_nose_x = sum(r['nose_x'] for r in st.initial_readings) / len(st.initial_readings)
            return progress_report()

        # --- 2. ACTION DETECTION ---
        # Blink Detection
        if not st.has_blinked:
            is_closed = ear_now < (st.baseline_ear * (1.0 - self.ear_drop_ratio))
            if is_closed:
                st.closed_frames += 1
                st.in_blink = True
            else:
                if st.in_blink and st.closed_frames >= self.min_closed_frames:
                    st.has_blinked = True # Action completed and remembered
                st.in_blink = False
                st.closed_frames = 0
        
        # Head Turn Detection
        deviation = nose_x - st.baseline_nose_x
        if not st.has_turned_right and deviation > self.nose_dx_threshold:
            st.has_turned_right = True # Action completed and remembered
        
        if not st.has_turned_left and deviation < -self.nose_dx_threshold:
            st.has_turned_left = True # Action completed and remembered

        # --- 3. SUCCESS CONDITION ---
        if st.has_blinked and st.has_turned_left and st.has_turned_right:
            ok, enc = cv2.imencode(".jpg", frame_bgr)
            return {
                "event": "success",
                "captured_frame_bytes": enc.tobytes() if ok else None,
            }

        # --- 4. RETURN PROGRESS ---
        return progress_report()
