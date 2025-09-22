// src/components/Unified_Check.jsx
import React, { useState, useRef, useEffect, useCallback } from 'react';
import { FaceLandmarker, FilesetResolver } from '@mediapipe/tasks-vision';

const UnifiedCheck = ({ crewName, igaCode, onComplete }) => {
  const [feedback, setFeedback] = useState('Please wait, loading AI model...');
  const [status, setStatus] = useState('loading'); // loading, ready, detecting, success, uploading, done
  const [groomingResult, setGroomingResult] = useState(null);
  const [error, setError] = useState(null);

  const videoRef = useRef(null);
  const streamRef = useRef(null);
  const landmarkerRef = useRef(null);
  const detectionLoopRef = useRef(null);

  const actionsDoneRef = useRef({ blink: false, left: false, right: false });
  const baselinesRef = useRef({ ear: null, noseX: null });

  const BASE_URL = import.meta.env.VITE_API_BASE_URL;
  const apiUrl = (path) => `${BASE_URL}${path}`;

  // Load MediaPipe model (WASM served from /public/wasm)
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

  const stopCheck = () => {
    cancelDetectionLoop();
    if (streamRef.current) {
      streamRef.current.getTracks().forEach((t) => t.stop());
      streamRef.current = null;
    }
    setStatus('ready');
  };

  const cancelDetectionLoop = () => {
    if (detectionLoopRef.current) {
      cancelAnimationFrame(detectionLoopRef.current);
      detectionLoopRef.current = null;
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

      // guard: ensure enough points exist (eye indices go up to ~468)
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
    setStatus('success');
    cancelDetectionLoop();

    const canvas = document.createElement('canvas');
    canvas.width = videoRef.current.videoWidth;
    canvas.height = videoRef.current.videoHeight;
    canvas.getContext('2d').drawImage(videoRef.current, 0, 0);

    // compress snapshot to keep backend fast
    const imageB64 = canvas.toDataURL('image/jpeg', 0.8).split(',')[1];

    stopCheck();
    setStatus('uploading');
    runGroomingPhoto(imageB64);
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

      // New clean shape
      const clean = data?.result || null;

      // Fallback to old keys if backend not yet updated
      const fallbackParsed = data?.parsed || null;
      const normalized =
        clean ||
        (fallbackParsed
          ? {
              assessment: fallbackParsed.overall_assessment || 'UNKNOWN',
              score: Number(fallbackParsed.overall_score || 0),
              details: {
                hairstyle: fallbackParsed?.details?.hairstyle || '',
                makeup: fallbackParsed?.details?.makeup || '',
                nails: fallbackParsed?.details?.nails || '',
                accessories: fallbackParsed?.details?.accessories || '',
                uniform: fallbackParsed?.details?.uniform || '',
              },
              issues: (fallbackParsed.issues_found || '')
                .split('\n')
                .filter(Boolean),
              recommendations: (fallbackParsed.recommendations || '')
                .split('\n')
                .filter(Boolean),
            }
          : null);

      setGroomingResult(normalized);
      setStatus('done');
      if (onComplete) onComplete();
    } catch (err) {
      setError('Grooming check failed.');
    }
  };

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
        <button
          className="ready-button"
          onClick={startCheck}
          disabled={!crewName || !igaCode}
          style={{ marginTop: 8 }}
        >
          Start Check
        </button>
      )}

      {status === 'detecting' && (
        <button className="stop-button" onClick={stopCheck} style={{ marginTop: 8 }}>
          Cancel
        </button>
      )}

      {(status === 'uploading') && <p className="loader">Analyzing...</p>}

      {status === 'done' && groomingResult && (
        <div className="result-card" style={{ marginTop: 12 }}>
          <h3>Grooming Result</h3>

          <div style={{ marginBottom: 8 }}>
            <span
              style={{
                padding: '4px 8px',
                borderRadius: 6,
                color: '#fff',
                background:
                  groomingResult.assessment === 'COMPLIANT' ? '#16a34a' : '#dc2626',
              }}
            >
              {groomingResult.assessment}
            </span>
            <span style={{ marginLeft: 10 }}>Score: {groomingResult.score}/10</span>
          </div>

          <div
            style={{
              display: 'grid',
              gridTemplateColumns: '150px 1fr',
              rowGap: 6,
              columnGap: 12,
            }}
          >
            <div style={{ fontWeight: 600 }}>Uniform</div>
            <div>{groomingResult.details?.uniform || '-'}</div>
            <div style={{ fontWeight: 600 }}>Hairstyle</div>
            <div>{groomingResult.details?.hairstyle || '-'}</div>
            <div style={{ fontWeight: 600 }}>Makeup</div>
            <div>{groomingResult.details?.makeup || '-'}</div>
            <div style={{ fontWeight: 600 }}>Nails</div>
            <div>{groomingResult.details?.nails || '-'}</div>
            <div style={{ fontWeight: 600 }}>Accessories</div>
            <div>{groomingResult.details?.accessories || '-'}</div>
          </div>

          {Array.isArray(groomingResult.issues) && groomingResult.issues.length > 0 && (
            <>
              <h4 style={{ marginTop: 12 }}>Issues</h4>
              <ul>
                {groomingResult.issues.map((it, i) => (
                  <li key={i}>{it}</li>
                ))}
              </ul>
            </>
          )}

          {Array.isArray(groomingResult.recommendations) &&
            groomingResult.recommendations.length > 0 && (
              <>
                <h4 style={{ marginTop: 12 }}>Recommendations</h4>
                <ul>
                  {groomingResult.recommendations.map((it, i) => (
                    <li key={i}>{it}</li>
                  ))}
                </ul>
              </>
            )}
        </div>
      )}

      {error && <div className="error-message">{error}</div>}
    </div>
  );
};

export default UnifiedCheck;
