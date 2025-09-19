

import React, { useState, useRef, useImperativeHandle, forwardRef } from 'react';

/**
 * UnifiedCheck (Web-only, HTTP frame streaming)
 *
 * Flow:
 * 1) Start camera preview.
 * 2) Every ~300ms, draw <video> to <canvas>, convert to base64 JPEG, POST to /liveliness-frame.
 * 3) When server replies with { event: "success", captured_frame_b64 }, stop camera and call /check-grooming.
 *
 * Props:
 *  - crewName: string
 *  - igaCode: string
 *  - onComplete: function (called after grooming report is set)
 */
const UnifiedCheck = forwardRef(({ crewName, igaCode, onComplete }, ref) => {
  // UI state
  const [streaming, setStreaming] = useState(false);
  const [showTick, setShowTick] = useState(false);
  const [loadingGrooming, setLoadingGrooming] = useState(false);
  const [groomingResult, setGroomingResult] = useState(null);
  const [error, setError] = useState(null);
  const [blinkCount, setBlinkCount] = useState(0);

  // Refs
  const videoRef = useRef(null);
  const canvasRef = useRef(null);
  const streamRef = useRef(null);
  const frameTimerRef = useRef(null);

  // Config
  const BASE_URL = import.meta.env.VITE_API_BASE_URL || ''; // e.g., http://localhost:8000
  const FRAME_INTERVAL_MS = 300; // ~3.3 fps
  const MAX_WIDTH = 640;         // scale frames down for bandwidth

  useImperativeHandle(ref, () => ({
    startUnifiedCheck,
    stopUnifiedCheck,
  }));

  const apiUrl = (path) => {
    const base = BASE_URL.replace(/\/+$/, '');
    return base ? `${base}${path}` : `/api${path}`; // proxy fallback if you use Vite proxy
  };

  const waitForVideoReady = () =>
    new Promise((resolve) => {
      const v = videoRef.current;
      if (!v) return resolve();
      if (v.readyState >= 2) return resolve();
      v.onloadeddata = () => resolve();
    });

  const startCamera = async () => {
    const stream = await navigator.mediaDevices.getUserMedia({ video: true, audio: false });
    streamRef.current = stream;
    if (videoRef.current) {
      videoRef.current.srcObject = stream;
      // For iOS Safari compatibility
      try { await videoRef.current.play(); } catch {}
      await waitForVideoReady();
    }
  };

  const stopCamera = () => {
    if (frameTimerRef.current) {
      clearInterval(frameTimerRef.current);
      frameTimerRef.current = null;
    }
    const stream = streamRef.current;
    if (stream) {
      stream.getTracks().forEach((t) => t.stop());
      streamRef.current = null;
    }
    if (videoRef.current) videoRef.current.srcObject = null;
  };

  const canvasFrameBase64 = (maxW = MAX_WIDTH, quality = 0.6) => {
    const video = videoRef.current;
    if (!video) return null;
    const vw = video.videoWidth || 640;
    const vh = video.videoHeight || 480;

    const scale = Math.min(1, maxW / vw);
    const cw = Math.round(vw * scale);
    const ch = Math.round(vh * scale);

    let canvas = canvasRef.current;
    if (!canvas) {
      canvas = document.createElement('canvas');
      canvasRef.current = canvas;
    }
    canvas.width = cw;
    canvas.height = ch;

    const ctx = canvas.getContext('2d');
    ctx.drawImage(video, 0, 0, cw, ch);
    const dataUrl = canvas.toDataURL('image/jpeg', quality);
    return dataUrl.split(',')[1]; // strip data URL prefix
  };

  async function startUnifiedCheck() {
    // Reset UI
    setStreaming(false);
    setShowTick(false);
    setLoadingGrooming(false);
    setGroomingResult(null);
    setError(null);
    setBlinkCount(0);

    await startCamera();
    setStreaming(true);

    const sessionId = `sess_${Date.now()}_${Math.random().toString(16).slice(2, 8)}`;

    frameTimerRef.current = setInterval(async () => {
      try {
        const frameB64 = canvasFrameBase64();
        if (!frameB64) return;

        const res = await fetch(apiUrl('/liveliness-frame'), {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            sessionId,
            crewName,
            igaCode,
            frameBase64: frameB64,
          }),
        });

        if (!res.ok) {
          const text = await res.text().catch(() => '');
          throw new Error(`HTTP ${res.status} ${res.statusText} – ${text}`);
        }

        const msg = await res.json();

        if (msg.event === 'progress') {
          if (typeof msg.blink_count === 'number') setBlinkCount(msg.blink_count);
        } else if (msg.event === 'success') {
          // Stop stream immediately
          await stopUnifiedCheck();
          setShowTick(true);

          // Send the captured frame to grooming
          const imageB64 = msg.captured_frame_b64 || msg.imageBase64;
          setTimeout(async () => {
            setShowTick(false);
            await runGrooming(imageB64);
          }, 600);
        } else if (msg.event === 'error') {
          setError(msg.message || 'Unexpected error in liveliness.');
        }
      } catch (err) {
        console.error('Streaming error:', err);
        setError('❌ Liveliness streaming failed. Please try again.');
        await stopUnifiedCheck();
      }
    }, FRAME_INTERVAL_MS);
  }

  async function stopUnifiedCheck() {
    stopCamera();
    setStreaming(false);
  }

  async function runGrooming(imageB64) {
    try {
      setLoadingGrooming(true);
      setGroomingResult(null);

      // Ensure data URL prefix (your API accepts either)
      const payloadBase64 = imageB64?.startsWith('data:')
        ? imageB64
        : `data:image/jpeg;base64,${imageB64 || ''}`;

      const res = await fetch(apiUrl('/check-grooming'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          imageBase64: payloadBase64,
          crewName,
          igaCode,
        }),
      });

      if (!res.ok) {
        const text = await res.text().catch(() => '');
        throw new Error(`HTTP ${res.status} ${res.statusText} – ${text}`);
      }

      const data = await res.json();
      setGroomingResult(data.result || JSON.stringify(data, null, 2));
      if (typeof onComplete === 'function') onComplete();
    } catch (err) {
      console.error('Grooming error:', err);
      setError('❌ Grooming check failed.');
    } finally {
      setLoadingGrooming(false);
    }
  }

  return (
    <div className="unified-check stylish-form" id="test-section">
      <h2>🧠 Grooming + Liveliness Check (Web Streaming)</h2>

      <div className="camera-section">
        <video ref={videoRef} autoPlay muted playsInline className="webcam-video" />
      </div>

      {streaming && (
        <div className="instructions enhanced-instruction">
          🔄 <strong>Turn your head (left & right) and blink naturally</strong>
          {blinkCount > 0 ? <span style={{ marginLeft: 8 }}>(Blinks: {blinkCount})</span> : null}
        </div>
      )}

      {showTick && <div className="tick-animation">✅</div>}

      {loadingGrooming && (
        <div className="loading-section">
          <div className="spinner" />
          <p>⏳ Generating grooming report...</p>
        </div>
      )}

      {groomingResult && (
        <div className="result-card">
          <h3>Grooming Result</h3>
          <pre className="result-text">{groomingResult}</pre>
        </div>
      )}

      {error && <div className="error-message">{error}</div>}
    </div>
  );
});

export default UnifiedCheck;
