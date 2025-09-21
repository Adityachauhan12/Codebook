import React, {
  useState,
  useRef,
  useImperativeHandle,
  forwardRef,
  useEffect,
} from 'react';

/**
 * Simplified and robust UnifiedCheck component.
 *
 * Flow:
 * 1. User selects "Photo" or "Live Video" and clicks "Start".
 * 2. Status -> 'liveliness': Camera starts, streams frames for liveliness check.
 * 3. On liveliness success: Status -> 'success'.
 * - Photo Mode: Automatically captures frame, stops camera, and runs grooming.
 * - Video Mode: Keeps camera preview on, shows record buttons.
 * 4. User records video (if in video mode).
 * 5. On completion, video is uploaded for grooming. Camera is stopped.
 *
 * Props:
 * - crewName, igaCode, onComplete
 */
const UnifiedCheck = forwardRef(
  ({ crewName = '', igaCode = '', onComplete }, ref) => {
    // --- STATE MANAGEMENT ---
    const [status, setStatus] = useState('idle'); // 'idle' | 'liveliness' | 'success' | 'recording' | 'uploading' | 'done'
    const [mode, setMode] = useState('photo');
    const [groomingResult, setGroomingResult] = useState(null);
    const [error, setError] = useState(null);
    const [actionsDone, setActionsDone] = useState({ blink: false, left: false, right: false });
    const [recordMs, setRecordMs] = useState(0);
    const MAX_VIDEO_MS = 15000;

    // --- REFS ---
    const videoRef = useRef(null);
    const streamRef = useRef(null);
    const livelinessIntervalRef = useRef(null);
    const mediaRecorderRef = useRef(null);
    const recordedChunksRef = useRef([]);
    const recordTimerRef = useRef(null);
    const sessionIdRef = useRef('');

    // --- CONFIG ---
    const BASE_URL = import.meta.env.VITE_API_BASE_URL || '';
    const FRAME_INTERVAL_MS = 330; // ~3 FPS

    useImperativeHandle(ref, () => ({
      startUnifiedCheck,
      stopUnifiedCheck,
    }));

    // Cleanup effect to stop camera when component unmounts
    useEffect(() => {
      return () => {
        stopCamera();
        cleanTimers();
      };
    }, []);

    const apiUrl = (path) => `${BASE_URL}${path}`;

    // --- CAMERA & STREAM CONTROLS ---
    async function startCamera() {
      if (streamRef.current) return; // Already running
      try {
        const constraints = {
          video: { facingMode: 'user', width: { ideal: 1280 }, height: { ideal: 720 } },
          audio: mode === 'video', // Only request mic for video mode
        };
        const stream = await navigator.mediaDevices.getUserMedia(constraints);
        streamRef.current = stream;
        if (videoRef.current) {
          videoRef.current.srcObject = stream;
          await videoRef.current.play();
        }
      } catch (err) {
        console.error('Camera error:', err);
        setError('❌ Camera permission denied. Please allow camera access in your browser settings.');
        setStatus('idle');
        throw err; // Propagate error
      }
    }

    function stopCamera() {
      if (streamRef.current) {
        streamRef.current.getTracks().forEach((track) => track.stop());
        streamRef.current = null;
      }
      if (videoRef.current) {
        videoRef.current.srcObject = null;
      }
    }
    
    function cleanTimers() {
        clearInterval(livelinessIntervalRef.current);
        livelinessIntervalRef.current = null;
        clearInterval(recordTimerRef.current);
        recordTimerRef.current = null;
    }

    // --- CORE LOGIC ---
    function resetState() {
      setGroomingResult(null);
      setError(null);
      setActionsDone({ blink: false, left: false, right: false });
      setRecordMs(0);
      recordedChunksRef.current = [];
      cleanTimers();
    }

    async function startUnifiedCheck() {
      resetState();
      stopCamera(); // Ensure previous stream is stopped before starting a new one
      setStatus('liveliness');
      sessionIdRef.current = `sess_${Date.now()}_${Math.random().toString(16).slice(2, 8)}`;

      try {
        await startCamera();
        livelinessIntervalRef.current = setInterval(runLivelinessFrame, FRAME_INTERVAL_MS);
      } catch {
        // Error is already set by startCamera()
      }
    }
    
    function stopUnifiedCheck() {
        stopCamera();
        resetState();
        setStatus('idle');
    }

    async function runLivelinessFrame() {
      if (!videoRef.current || videoRef.current.paused || videoRef.current.ended) return;

      // Get Base64 frame from video element
      const canvas = document.createElement('canvas');
      const video = videoRef.current;
      const scale = 640 / (video.videoWidth || 640);
      canvas.width = video.videoWidth * scale;
      canvas.height = video.videoHeight * scale;
      canvas.getContext('2d').drawImage(video, 0, 0, canvas.width, canvas.height);
      const frameB64 = canvas.toDataURL('image/jpeg', 0.7).split(',')[1];
      if (!frameB64) return;

      try {
        const res = await fetch(apiUrl('/liveliness-frame'), {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            sessionId: sessionIdRef.current,
            crewName,
            igaCode,
            frameBase64: frameB64,
          }),
        });

        if (!res.ok) throw new Error(`Server error: ${res.status}`);
        const data = await res.json();

        if (data.event === 'progress') {
          setActionsDone(data.actions_done || { blink: false, left: false, right: false });
        } else if (data.event === 'success') {
          cleanTimers(); // Stop sending frames
          setStatus('success');

          if (mode === 'photo') {
            // For photos, the flow is fully automated after success
            setStatus('uploading');
            runGroomingPhoto(data.captured_frame_b64);
          }
          // For video, we simply stop and wait for the user to click record.
        }
      } catch (err) {
        console.error('Liveliness fetch error:', err);
        setError('❌ Liveliness check failed. Please try again.');
        stopUnifiedCheck();
      }
    }

    // --- GROOMING SUBMISSION ---
    async function runGroomingPhoto(imageBase64) {
      try {
        const res = await fetch(apiUrl('/check-grooming'), {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ imageBase64, crewName, igaCode }),
        });
        if (!res.ok) throw new Error('Grooming API request failed');
        const data = await res.json();
        setGroomingResult(data.display_text || JSON.stringify(data, null, 2));
        setStatus('done');
        if (onComplete) onComplete();
      } catch (err) {
        console.error('Grooming (photo) error:', err);
        setError('❌ Grooming check failed.');
      } finally {
        stopCamera(); // Final cleanup
      }
    }

    async function uploadVideoForGrooming(blob) {
      setStatus('uploading');
      try {
        const formData = new FormData();
        formData.append('video', blob, `grooming-${igaCode}.webm`);
        formData.append('name', crewName);
        formData.append('iga_code', igaCode);

        const res = await fetch(apiUrl('/check-grooming-video'), {
          method: 'POST',
          body: formData,
        });
        if (!res.ok) throw new Error('Video grooming API request failed');
        const data = await res.json();
        setGroomingResult(data.result || JSON.stringify(data, null, 2));
        setStatus('done');
        if (onComplete) onComplete();
      } catch (err) {
        console.error('Grooming (video) error:', err);
        setError('❌ Video grooming check failed.');
      } finally {
        stopCamera(); // Final cleanup
      }
    }

    // --- VIDEO RECORDING ---
    function startRecording() {
      if (!streamRef.current) {
        setError('Camera stream not found.');
        return;
      }
      
      const mimeType = 'video/webm;codecs=vp9,opus';
      if (!MediaRecorder.isTypeSupported(mimeType)) {
          setError('Browser does not support required video format (WebM).');
          return;
      }
      
      recordedChunksRef.current = [];
      mediaRecorderRef.current = new MediaRecorder(streamRef.current, { mimeType });

      mediaRecorderRef.current.ondataavailable = (event) => {
        if (event.data && event.data.size > 0) {
          recordedChunksRef.current.push(event.data);
        }
      };

      mediaRecorderRef.current.onstop = () => {
        const blob = new Blob(recordedChunksRef.current, { type: mimeType });
        uploadVideoForGrooming(blob);
      };

      mediaRecorderRef.current.start();
      setStatus('recording');

      // Start timer for UI
      recordTimerRef.current = setInterval(() => {
        setRecordMs((ms) => {
          const nextMs = ms + 100;
          if (nextMs >= MAX_VIDEO_MS) {
            stopRecording();
            return MAX_VIDEO_MS;
          }
          return nextMs;
        });
      }, 100);
    }

    function stopRecording() {
      if (mediaRecorderRef.current && mediaRecorderRef.current.state === 'recording') {
        mediaRecorderRef.current.stop();
      }
      cleanTimers();
    }

    const mmss = (ms) => new Date(ms).toISOString().slice(14, 19);

    // --- RENDER LOGIC ---
    const isCheckRunning = ['liveliness', 'success', 'recording', 'uploading'].includes(status);

    return (
      <div className="unified-check stylish-form" id="test-section">
        <h2>Liveliness + Grooming Check</h2>

        <div className="mode-selector">
          <label>
            <input type="radio" value="photo" checked={mode === 'photo'} onChange={(e) => setMode(e.target.value)} disabled={isCheckRunning} /> Photo
          </label>
          <label>
            <input type="radio" value="video" checked={mode === 'video'} onChange={(e) => setMode(e.target.value)} disabled={isCheckRunning} /> Video (≤15s)
          </label>
        </div>

        <div className="controls-section">
          {!isCheckRunning && (
              <button className="ready-button" onClick={startUnifiedCheck} disabled={!crewName || !igaCode}>
                  Start Check
              </button>
          )}
          {isCheckRunning && (
              <button className="stop-button" onClick={stopUnifiedCheck}>
                  Cancel
              </button>
          )}
        </div>

        <div className="camera-container">
            <video ref={videoRef} autoPlay muted playsInline />
            {status === 'liveliness' && (
                <div className="overlay-instructions">
                    <p>Please perform the following actions:</p>
                    <div className="action-list">
                        <span>Blink: {actionsDone.blink ? '✅' : '…'}</span>
                        <span>Turn Left: {actionsDone.left ? '✅' : '…'}</span>
                        <span>Turn Right: {actionsDone.right ? '✅' : '…'}</span>
                    </div>
                </div>
            )}
        </div>

        {status === 'success' && (
          <div className="success-message">
            ✅ Liveliness Passed!
            {mode === 'video' && (
                <div className="video-controls">
                    <p>Ready to record a short video.</p>
                    <button className="record-button start" onClick={startRecording}>⏺️ Start Recording</button>
                </div>
            )}
            {mode === 'photo' && <p>Running grooming check on captured photo...</p>}
          </div>
        )}
        
        {status === 'recording' && (
            <div className="video-controls recording">
                <span>Recording: {mmss(recordMs)} / {mmss(MAX_VIDEO_MS)}</span>
                <div className="progress-bar" style={{width: `${(recordMs / MAX_VIDEO_MS) * 100}%`}}></div>
                <button className="record-button stop" onClick={stopRecording}>⏹️ Stop</button>
            </div>
        )}

        {(status === 'uploading' || status === 'done') && groomingResult && (
          <div className="result-card">
            <h3>Grooming Result</h3>
            <pre className="result-text">{groomingResult}</pre>
          </div>
        )}

        {(status === 'uploading') && !groomingResult && (
             <p className="loader">⏳ Uploading and processing...</p>
        )}

        {error && <div className="error-message">{error}</div>}
      </div>
    );
  }
);

export default UnifiedCheck;
