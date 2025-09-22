// src/components/Unified_Check.jsx
import React, { useState, useRef, useEffect, useCallback } from 'react';
import { FaceLandmarker, FilesetResolver } from '@mediapipe/tasks-vision';

const UnifiedCheck = ({ crewName, igaCode, onComplete }) => {
  const [feedback, setFeedback] = useState('Please wait, loading AI model...');
  const [status, setStatus] = useState('loading'); // loading, ready, detecting, choose, uploading, done
  const [groomingResult, setGroomingResult] = useState(null);
  const [error, setError] = useState(null);

  const [mode, setMode] = useState(null); // 'photo' | 'video'
  const [recTimer, setRecTimer] = useState(0);
  const recIntervalRef = useRef(null);

  const videoRef = useRef(null);
  const streamRef = useRef(null);
  const landmarkerRef = useRef(null);
  const detectionLoopRef = useRef(null);

  const mediaRecorderRef = useRef(null);
  const recordedChunksRef = useRef([]);

  const actionsDoneRef = useRef({ blink: false, left: false, right: false });
  const baselinesRef = useRef({ ear: null, noseX: null });

  const BASE_URL = import.meta.env.VITE_API_BASE_URL;
  const apiUrl = (path) => `${BASE_URL}${path}`;

  // ---------- Load Face Landmarker ----------
  const loadModel = useCallback(async () => {
    try {
      const fileset = await FilesetResolver.forVisionTasks('/wasm');
      const landmarker = await FaceLandmarker.createFromOptions(fileset, {
        baseOptions: {
          modelAssetPath:
            'https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task',
        },
        runningMode: 'VIDEO',
        numFaces: 1,
      });
      landmarkerRef.current = landmarker;
      setStatus('ready');
      setFeedback('Ready to start the check.');
    } catch (e) {
      console.error(e);
      setError('Could not load AI model. Make sure the .wasm files are in public/wasm.');
    }
  }, []);

  useEffect(() => {
    loadModel();
    return () => cancelDetectionLoop();
  }, [loadModel]);

  // ---------- Liveness ----------
  const startCheck = async () => {
    if (status !== 'ready' || !landmarkerRef.current) return;
    actionsDoneRef.current = { blink: false, left: false, right: false };
    baselinesRef.current = { ear: null, noseX: null };

    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: 'user', width: { ideal: 1280 } },
      });
      streamRef.current = stream;
      if (videoRef.current) {
        videoRef.current.srcObject = stream;
        videoRef.current.onloadedmetadata = () => {
          runLiveDetectionLoop();
          setStatus('detecting');
        };
      }
    } catch (err) {
      setError('Camera permission denied.');
    }
  };

  const cancelDetectionLoop = () => {
    if (detectionLoopRef.current) {
      cancelAnimationFrame(detectionLoopRef.current);
      detectionLoopRef.current = null;
    }
  };

  const stopCamera = () => {
    if (streamRef.current) {
      streamRef.current.getTracks().forEach((t) => t.stop());
      streamRef.current = null;
    }
  };

  const runLiveDetectionLoop = () => {
    const video = videoRef.current;
    if (!video || video.paused || !landmarkerRef.current) {
      detectionLoopRef.current = requestAnimationFrame(runLiveDetectionLoop);
      return;
    }

    const results = landmarkerRef.current.detectForVideo(video, performance.now());
    if (results.faceLandmarks && results.faceLandmarks.length > 0) {
      const landmarks = results.faceLandmarks[0];
      if (!landmarks || landmarks.length < 470) {
        detectionLoopRef.current = requestAnimationFrame(runLiveDetectionLoop);
        return;
      }

      const LEYE = [33, 160, 158, 133, 153, 144];
      const REYE = [362, 385, 387, 263, 373, 380];
      const NOSE = 1;

      const dist = (p1, p2) =>
        Math.hypot(p1.x - p2.x, p1.y - p2.y, (p1.z ?? 0) - (p2.z ?? 0));
      const getEAR = (idx) => {
        const p1 = landmarks[idx[0]],
          p2 = landmarks[idx[1]],
          p3 = landmarks[idx[2]];
        const p4 = landmarks[idx[3]],
          p5 = landmarks[idx[4]],
          p6 = landmarks[idx[5]];
        return (dist(p2, p6) + dist(p3, p5)) / (2 * dist(p1, p4));
      };

      const ear = (getEAR(LEYE) + getEAR(REYE)) / 2;
      const noseX = landmarks[NOSE].x;

      if (baselinesRef.current.ear === null) baselinesRef.current.ear = ear;
      if (baselinesRef.current.noseX === null) baselinesRef.current.noseX = noseX;

      if (ear < baselinesRef.current.ear * 0.75) actionsDoneRef.current.blink = true;

      const deviation = noseX - baselinesRef.current.noseX;
      if (deviation < -0.03) actionsDoneRef.current.left = true;
      if (deviation > 0.03) actionsDoneRef.current.right = true;

      setFeedback(
        `${actionsDoneRef.current.blink ? 'Blink ✓' : 'Blink'} | ` +
          `${actionsDoneRef.current.left ? 'Left ✓' : 'Left'} | ` +
          `${actionsDoneRef.current.right ? 'Right ✓' : 'Right'}`
      );

      const { blink, left, right } = actionsDoneRef.current;
      if (blink && left && right) {
        onLivelinessSuccess();
        return;
      }
    }

    detectionLoopRef.current = requestAnimationFrame(runLiveDetectionLoop);
  };

  const onLivelinessSuccess = () => {
    cancelDetectionLoop();
    setStatus('choose');
    setFeedback('Liveliness passed. Choose photo or record a short video.');
  };

  // ---------- Photo path ----------
  const usePhotoAndUpload = () => {
    try {
      const canvas = document.createElement('canvas');
      canvas.width = videoRef.current.videoWidth;
      canvas.height = videoRef.current.videoHeight;
      canvas.getContext('2d').drawImage(videoRef.current, 0, 0);
      const imageB64 = canvas.toDataURL('image/jpeg', 0.8).split(',')[1];
      setMode('photo');
      setStatus('uploading');
      stopCamera();
      runGroomingPhoto(imageB64);
    } catch (e) {
      setError('Could not capture photo.');
    }
  };

  const runGroomingPhoto = async (imageBase64) => {
    try {
      const res = await fetch(apiUrl('/check-grooming'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ imageBase64, crewName, igaCode }),
      });
      if (!res.ok) throw new Error('API request failed');
      const data = await res.json();
      setGroomingResult(data?.result || null);
      setStatus('done');
      if (onComplete) onComplete();
    } catch {
      setError('Grooming check failed.');
    }
  };

  // ---------- Video path ----------
  const startRecording = () => {
    if (!streamRef.current) {
      setError('Camera stream not available.');
      return;
    }
    recordedChunksRef.current = [];
    setMode('video');
    setRecTimer(0);

    const options = { mimeType: 'video/webm;codecs=vp8' };
    const mr = new MediaRecorder(streamRef.current, options);
    mediaRecorderRef.current = mr;

    mr.ondataavailable = (e) => {
      if (e.data && e.data.size > 0) recordedChunksRef.current.push(e.data);
    };
    mr.onstop = handleRecordingStop;

    mr.start(100);
    if (recIntervalRef.current) clearInterval(recIntervalRef.current);
    recIntervalRef.current = setInterval(() => setRecTimer((t) => t + 1), 1000);

    // auto-stop at 10s
    setTimeout(() => {
      if (mr.state !== 'inactive') mr.stop();
    }, 10000);
  };

  const stopRecording = () => {
    if (mediaRecorderRef.current && mediaRecorderRef.current.state !== 'inactive') {
      mediaRecorderRef.current.stop();
    }
  };

  const handleRecordingStop = () => {
    if (recIntervalRef.current) {
      clearInterval(recIntervalRef.current);
      recIntervalRef.current = null;
    }
    const blob = new Blob(recordedChunksRef.current, { type: 'video/webm' });
    const file = new File([blob], 'grooming.webm', { type: 'video/webm' });
    const fd = new FormData();
    fd.append('video', file);
    fd.append('name', crewName);
    fd.append('iga_code', igaCode);
    setStatus('uploading');
    stopCamera();

    fetch(apiUrl('/check-grooming-video'), { method: 'POST', body: fd })
      .then((r) => r.json())
      .then((data) => {
        if (!data || data.status !== 'ok') throw new Error(data?.error || 'API error');
        setGroomingResult(data.result);
        setStatus('done');
        if (onComplete) onComplete();
      })
      .catch(() => setError('Video grooming check failed.'));
  };

  // ---------- Helpers for display ----------
  const row = (label, score, max, detail) => (
    <>
      <div style={{ fontWeight: 600 }}>{label}</div>
      <div>
        {score ?? '-'}{max ? `/${max}` : ''}{detail ? ` (${detail})` : ''}
      </div>
    </>
  );

  // ---------- UI ----------
  return (
    <div className="unified-check stylish-form">
      <h2>Liveliness & Grooming Check</h2>

      <div className="camera-container">
        <video
          ref={videoRef}
          autoPlay
          playsInline
          muted
          style={{ width: '100%', background: '#000', borderRadius: 8 }}
        />
        <p className="feedback-overlay" style={{ marginTop: 8 }}>
          {feedback}
        </p>
      </div>

      {status === 'ready' && (
        <button className="ready-button" onClick={startCheck} disabled={!crewName || !igaCode} style={{ marginTop: 8 }}>
          Start Check
        </button>
      )}

      {status === 'detecting' && (
        <button className="stop-button" onClick={() => { cancelDetectionLoop(); setStatus('ready'); }} style={{ marginTop: 8 }}>
          Cancel
        </button>
      )}

      {status === 'choose' && (
        <div style={{ marginTop: 10 }}>
          <p style={{ marginBottom: 8 }}>Choose how to do grooming:</p>
          <div style={{ display: 'flex', gap: 10 }}>
            <button onClick={usePhotoAndUpload}>Use Photo</button>
            <button onClick={startRecording}>Record 10s Video</button>
            {mode === 'video' && (
              <button onClick={stopRecording}>Stop Now ({recTimer}s)</button>
            )}
          </div>
        </div>
      )}

      {status === 'uploading' && <p className="loader">Analyzing...</p>}

      {status === 'done' && groomingResult && (
        <div className="result-card" style={{ marginTop: 12 }}>
          <h3>Grooming Result</h3>

          <p style={{ margin: '4px 0', color: '#555' }}>
            {groomingResult?.person?.name || crewName || '-'} • IGA: {groomingResult?.person?.iga_code || igaCode || '-'}
          </p>

          <div style={{ marginBottom: 8 }}>
            <span
              style={{
                padding: '4px 8px',
                borderRadius: 6,
                color: '#fff',
                background: groomingResult.assessment === 'COMPLIANT' ? '#16a34a' : '#dc2626',
              }}
            >
              {groomingResult.assessment}
            </span>
            <span style={{ marginLeft: 10 }}>Score: {groomingResult.score}/10</span>
          </div>

          <div style={{ marginTop: 6 }}>
            <h4>Category Scores</h4>
            <div style={{ display: 'grid', gridTemplateColumns: '150px 1fr', rowGap: 6, columnGap: 12 }}>
              {row('Uniform',     groomingResult.scores?.uniform, 3, groomingResult.details?.uniform)}
              {row('Hairstyle',   groomingResult.scores?.hairstyle, 2, groomingResult.details?.hairstyle)}
              {row('Makeup',      groomingResult.scores?.makeup, 2, groomingResult.details?.makeup)}
              {row('Nails',       groomingResult.scores?.nails, 1, groomingResult.details?.nails)}
              {row('Accessories', groomingResult.scores?.accessories, 2, groomingResult.details?.accessories)}
            </div>
          </div>

          {Array.isArray(groomingResult.issues) && groomingResult.issues.length > 0 && (
            <>
              <h4 style={{ marginTop: 12 }}>Issues</h4>
              <ul>{groomingResult.issues.map((it, i) => <li key={i}>{it}</li>)}</ul>
            </>
          )}

          {Array.isArray(groomingResult.recommendations) && groomingResult.recommendations.length > 0 && (
            <>
              <h4 style={{ marginTop: 12 }}>Recommendations</h4>
              <ul>{groomingResult.recommendations.map((it, i) => <li key={i}>{it}</li>)}</ul>
            </>
          )}
        </div>
      )}

      {error && <div className="error-message">{error}</div>}
    </div>
  );
};

export default UnifiedCheck;
