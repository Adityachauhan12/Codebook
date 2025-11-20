import React, { useState, useEffect, useCallback } from 'react';
import { FaceLandmarker, FilesetResolver, NormalizedLandmark } from '@mediapipe/tasks-vision';
import { UnifiedCheckProps } from '../types';
import { apiConfig } from '../utils/groomingHelpers';

interface UnifiedCheckPropsExtended extends UnifiedCheckProps {
  onUploadVideo?: () => void;
}

interface GroomingResult {
  assessment: string;
  score: number;
  scores?: {
    uniform: number;
    hairstyle: number;
    makeup: number;
    nails: number;
    accessories: number;
  };
  issues?: string[];
  recommendations?: string[];
}

type Status =
  | 'loading'
  | 'ready'
  | 'chooseLive'   // choose Photo or 10s Live Video (after selecting "Do Live Check")
  | 'detecting'    // running liveliness for Photo flow
  | 'recording'    // recording 10s and running liveliness for Video flow
  | 'uploading'
  | 'done';

const UnifiedCheck: React.FC<UnifiedCheckPropsExtended> = ({
  crewName,
  igaCode,
  onComplete,
  onCancel,
  onUploadVideo,
}) => {
  /** -------------------- STATE -------------------- **/
  const [status, setStatus] = useState<Status>('loading');
  const [feedback, setFeedback] = useState<string>('Please wait, loading AI model...');
  const [declarationAccepted, setDeclarationAccepted] = useState<boolean>(false);
  const [showDisclaimer, setShowDisclaimer] = useState<boolean>(false);
  const [groomingResult, setGroomingResult] = useState<GroomingResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [recTimer, setRecTimer] = useState<number>(0);

  /** -------------------- REFS -------------------- **/
  const videoRef = React.useRef<HTMLVideoElement>(null);
  const streamRef = React.useRef<MediaStream | null>(null);
  const landmarkerRef = React.useRef<FaceLandmarker | null>(null);
  const detectionLoopRef = React.useRef<number | null>(null);

  // liveliness actions tracking
  const actionsDoneRef = React.useRef({ blink: false });
  const baselinesRef = React.useRef<{
    ear: number | null;
    faceWidth: number | null;
    faceHeight: number | null;
  }>({ ear: null, faceWidth: null, faceHeight: null });

  // flow control
  const flowRef = React.useRef<'photo' | 'video' | null>(null);
  const livenessPassedRef = React.useRef<boolean>(false);

  // recording
  const mediaRecorderRef = React.useRef<MediaRecorder | null>(null);
  const recordedChunksRef = React.useRef<Blob[]>([]);
  const recIntervalRef = React.useRef<NodeJS.Timeout | null>(null);

  /** -------------------- HELPERS -------------------- **/
  const apiUrl = (path: string) =>
    `${apiConfig.baseUrl}${path.startsWith('/') ? '' : '/'}${path}`;

  const cancelDetectionLoop = useCallback(() => {
    if (detectionLoopRef.current) {
      cancelAnimationFrame(detectionLoopRef.current);
      detectionLoopRef.current = null;
      console.log('Detection loop cancelled');
    }
  }, []);

  const stopCamera = useCallback(() => {
    if (streamRef.current) {
      streamRef.current.getTracks().forEach((t) => t.stop());
      streamRef.current = null;
      console.log('Camera stopped');
    }
  }, []);

  const startCamera = useCallback(async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: 'user', width: { ideal: 1280 } },
      });
      streamRef.current = stream;
      if (videoRef.current) {
        videoRef.current.srcObject = stream;
        // Ensure video plays; detection loop will check pause/ready state
        await videoRef.current.play().catch(() => void 0);
      }
      return true;
    } catch (e) {
      setError('Camera permission denied.');
      return false;
    }
  }, []);

  // ⭐ ADD ONLY THESE TWO LINES - don't touch existing state
  const [selectedBase, setSelectedBase] = useState<string>("");
  const bases = ["DEL", "AMD", "BOM", "CCU", "MAA", "HYD", "LKO", "PNQ", "COK", "IXC", "IDR", "JAI", "BLR"];

  /** -------------------- DETECTION LOOP -------------------- **/
  const runLiveDetectionLoop = useCallback(() => {
    const video = videoRef.current;

    // keep the loop going regardless; we'll exit when status changes
    const loop = () => {
      if (!video || video.paused || !landmarkerRef.current) {
        detectionLoopRef.current = requestAnimationFrame(loop);
        return;
      }

      // Only run detection during detecting/recording
      if (status !== 'detecting' && status !== 'recording') {
        return;
      }

      try {
        const results = landmarkerRef.current.detectForVideo(video, performance.now());
        if (results.faceLandmarks && results.faceLandmarks.length > 0) {
          const landmarks = results.faceLandmarks[0];
          if (!landmarks || landmarks.length < 470) {
            detectionLoopRef.current = requestAnimationFrame(loop);
            return;
          }

          // indices
          const LEYE = [33, 160, 158, 133, 153, 144];
          const REYE = [362, 385, 387, 263, 373, 380];
          const CHIN = 152;
          const FOREHEAD = 10;

          const dist = (p1: NormalizedLandmark, p2: NormalizedLandmark): number =>
            Math.hypot(p1.x - p2.x, p1.y - p2.y, (p1.z ?? 0) - (p2.z ?? 0));

          const getEAR = (idx: number[]): number => {
            const p1 = landmarks[idx[0]],
              p2 = landmarks[idx[1]],
              p3 = landmarks[idx[2]];
            const p4 = landmarks[idx[3]],
              p5 = landmarks[idx[4]],
              p6 = landmarks[idx[5]];
            return (dist(p2, p6) + dist(p3, p5)) / (2 * dist(p1, p4));
          };

          const ear = (getEAR(LEYE) + getEAR(REYE)) / 2;

          // Calculate face metrics for validation
          const leftEyeCenter = {
            x: (landmarks[33].x + landmarks[133].x) / 2,
            y: (landmarks[33].y + landmarks[133].y) / 2,
            z: ((landmarks[33].z ?? 0) + (landmarks[133].z ?? 0)) / 2
          };
          const rightEyeCenter = {
            x: (landmarks[362].x + landmarks[263].x) / 2,
            y: (landmarks[362].y + landmarks[263].y) / 2,
            z: ((landmarks[362].z ?? 0) + (landmarks[263].z ?? 0)) / 2
          };

          // Face width (inter-eye distance) - should remain relatively stable
          const faceWidth = dist(leftEyeCenter as NormalizedLandmark, rightEyeCenter as NormalizedLandmark);

          // Face height (forehead to chin) - should remain relatively stable
          const faceHeight = dist(landmarks[FOREHEAD], landmarks[CHIN]);

          // Initialize baselines
          if (baselinesRef.current.ear === null) baselinesRef.current.ear = ear;
          if (baselinesRef.current.faceWidth === null) baselinesRef.current.faceWidth = faceWidth;
          if (baselinesRef.current.faceHeight === null) baselinesRef.current.faceHeight = faceHeight;

          // Validate face stability - reject if face size changed significantly
          const widthDeviation = Math.abs(faceWidth - (baselinesRef.current.faceWidth ?? faceWidth));
          const heightDeviation = Math.abs(faceHeight - (baselinesRef.current.faceHeight ?? faceHeight));

          // Face should not move more than 2% in size (prevents hand covering face or moving away)
          const isFaceStable = widthDeviation < 0.02 && heightDeviation < 0.02;

          if (!isFaceStable) {
            // Face size changed significantly - likely occlusion or movement, don't count gestures
            detectionLoopRef.current = requestAnimationFrame(loop);
            return;
          }

          // Check if eyes are clearly visible (both eye aspect ratios should be reasonable)
          const leftEAR = getEAR(LEYE);
          const rightEAR = getEAR(REYE);
          const eyesVisible = leftEAR > 0.1 && rightEAR > 0.1 && Math.abs(leftEAR - rightEAR) < 0.15;

          if (!eyesVisible) {
            // Eyes not clearly visible - likely occlusion, don't count gestures
            detectionLoopRef.current = requestAnimationFrame(loop);
            return;
          }

          // Blink detection - only when face is stable and eyes were visible
          if (baselinesRef.current.ear !== null && ear < baselinesRef.current.ear * 0.8) {
            // Additional check: both eyes should close similarly for a real blink
            if (Math.abs(leftEAR - rightEAR) < 0.08) {
              if (!actionsDoneRef.current.blink) actionsDoneRef.current.blink = true;
            }
          }

          setFeedback(
            `${actionsDoneRef.current.blink ? 'Blink ✓' : 'Blink'}`
          );

          // Passed liveliness
          if (actionsDoneRef.current.blink) {
            livenessPassedRef.current = true;

            if (flowRef.current === 'photo' && status === 'detecting') {
              // Capture the frame immediately and upload
              handlePhotoPassAndUpload();
              return; // stop loop after capture
            }

            // For video flow we continue recording; on stop, we decide to upload
          }
        } else {
          setFeedback('No face detected - please position yourself in the camera');
        }
      } catch (err) {
        console.error('Detection loop error:', err);
      }

      detectionLoopRef.current = requestAnimationFrame(loop);
    };

    detectionLoopRef.current = requestAnimationFrame(loop);
  }, [status]);

  useEffect(() => {
    if ((status === 'detecting' || status === 'recording') && !detectionLoopRef.current) {
      runLiveDetectionLoop();
    }
  }, [status, runLiveDetectionLoop]);

  /** -------------------- MODEL LOAD / CLEANUP -------------------- **/
  const loadModel = useCallback(async () => {
    try {
      const fileset = await FilesetResolver.forVisionTasks(apiConfig.visionWasmBase);
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
      setFeedback('Choose how you want to continue.');
    } catch (err) {
      console.error('Model loading error:', err);
      setError('Could not load AI model. Please refresh and try again.');
    }
  }, []);

  useEffect(() => {
    loadModel();
    return () => {
      cancelDetectionLoop();
      stopCamera();
    };
  }, [loadModel, cancelDetectionLoop, stopCamera]);

  /** -------------------- FLOWS -------------------- **/
  const resetLiveliness = () => {
    actionsDoneRef.current = { blink: false };
    baselinesRef.current = { ear: null, faceWidth: null, faceHeight: null };
    livenessPassedRef.current = false;
  };

  // Entry from "Do Live Check"
  const goToLiveOptions = useCallback(() => {
    setStatus('chooseLive');
    setFeedback('Choose your live assessment method.');
  }, []);

  /** -------- Photo flow: start + auto-capture on pass -------- **/
  const startPhotoFlow = useCallback(async () => {
    if (!declarationAccepted || !crewName || !igaCode) return;

    resetLiveliness();
    flowRef.current = 'photo';

    const ok = await startCamera();
    if (!ok) return;

    setStatus('detecting');
    setFeedback('Look at the camera. Blink to pass liveliness. We will auto-capture on pass.');
  }, [declarationAccepted, crewName, igaCode, startCamera]);

  const handlePhotoPassAndUpload = useCallback(() => {
    try {
      cancelDetectionLoop();
      // Capture current frame
      const canvas = document.createElement('canvas');
      const video = videoRef.current;
      if (!video) return;

      canvas.width = video.videoWidth;
      canvas.height = video.videoHeight;
      const ctx = canvas.getContext('2d');
      if (!ctx) return;

      ctx.drawImage(video, 0, 0);
      const imageB64 = canvas.toDataURL('image/jpeg', 0.8).split(',')[1];

      setStatus('uploading');
      stopCamera();

      runGroomingPhoto(imageB64);
    } catch (e) {
      console.error(e);
      setError('Could not capture photo.');
    }
  }, []);

  const runGroomingPhoto = async (imageBase64: string) => {
  // ⭐ ADD THIS VALIDATION AT THE TOP
  if (!selectedBase) {
    setError("⚠️ Please select a base before submitting");
    return;
  }

  try {
    const res = await fetch(apiUrl("check-grooming"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ 
        imageBase64, 
        crewName, 
        igaCode,
        base: selectedBase  // ⭐ ADD THIS LINE
      }),
    });
    
    if (!res.ok) throw new Error(`HTTP ${res.status}: ${await res.text()}`);
    const data = await res.json();
    setGroomingResult(data?.result ?? null);
    setStatus("done");
  } catch (e) {
    console.error(e);
    setError("Grooming check failed.");
  }
};


  /** -------- Live Video (10s) flow: record + pass check -------- **/
  const startVideoFlow = useCallback(async () => {
    if (!declarationAccepted || !crewName || !igaCode) return;

    resetLiveliness();
    flowRef.current = 'video';

    const ok = await startCamera();
    if (!ok) return;

    // prepare recording
    recordedChunksRef.current = [];
    setRecTimer(0);

    const options: MediaRecorderOptions = { mimeType: 'video/webm;codecs=vp8' };
    let mr: MediaRecorder;
    try {
      mr = new MediaRecorder(streamRef.current as MediaStream, options);
    } catch (err) {
      setError('Unable to start video recording.');
      return;
    }

    mediaRecorderRef.current = mr;
    mr.ondataavailable = (e) => {
      if (e.data && e.data.size > 0) recordedChunksRef.current.push(e.data);
    };
    mr.onstop = handleRecordingStop;

    setStatus('recording');
    setFeedback('Recording for 10s. Blink to pass liveliness.');

    // timer for UI
    if (recIntervalRef.current) clearInterval(recIntervalRef.current);
    recIntervalRef.current = setInterval(() => setRecTimer((t) => t + 1), 1000);

    mr.start(100); // gather in chunks

    // Stop automatically at 10s
    setTimeout(() => {
      if (mr.state !== 'inactive') mr.stop();
    }, 10000);
  }, [declarationAccepted, crewName, igaCode, startCamera]);

  const stopRecording = useCallback(() => {
    if (mediaRecorderRef.current && mediaRecorderRef.current.state !== 'inactive') {
      mediaRecorderRef.current.stop();
    }
  }, []);

  const handleRecordingStop = useCallback(() => {
    if (recIntervalRef.current) {
      clearInterval(recIntervalRef.current);
      recIntervalRef.current = null;
    }

    // If liveliness failed within 10s → do not upload
    if (!livenessPassedRef.current) {
      cancelDetectionLoop();
      stopCamera();
      setStatus('ready');
      setError('Liveliness failed. Video will not be sent for assessment.');
      return;
    }

    // Liveliness passed → upload the 10s clip
    const blob = new Blob(recordedChunksRef.current, { type: 'video/webm' });
    const file = new File([blob], 'grooming.webm', { type: 'video/webm' });
    const fd = new FormData();
    fd.append("video", file);
    fd.append("name", crewName);
    fd.append("igacode", igaCode);
    fd.append("base", selectedBase);  // ⭐ ADD THIS LINE

    setStatus('uploading');
    cancelDetectionLoop();
    stopCamera();

    fetch(apiUrl('/check-grooming-video'), { method: 'POST', body: fd })
      .then(async (r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status} ${await r.text()}`);
        return r.json();
      })
      .then((data) => {
        if (!data || data.status !== 'ok') throw new Error(data?.error ?? 'API error');
        setGroomingResult(data.result);
        setStatus('done');
      })
      .catch((e) => {
        console.error(e);
        setError('Video grooming check failed.');
      });
  }, [cancelDetectionLoop, stopCamera, crewName, igaCode]);

  /** -------------------- CANCEL / NAV -------------------- **/
  const handleCancel = useCallback(() => {
    cancelDetectionLoop();
    stopCamera();
    setStatus('ready');
    if (onCancel) onCancel();
  }, [cancelDetectionLoop, stopCamera, onCancel]);

  const handleBackToDashboard = useCallback(() => {
    if (onComplete && groomingResult) {
      onComplete(groomingResult);
    } else if (onCancel) {
      onCancel();
    }
  }, [onComplete, onCancel, groomingResult]);

  const handleNewAssessment = useCallback(() => {
    setStatus('ready');
    setGroomingResult(null);
    setError(null);
    setFeedback('Choose how you want to continue.');
    setDeclarationAccepted(false);
    setShowDisclaimer(false);
    resetLiveliness();
    flowRef.current = null;
    recordedChunksRef.current = [];
    setRecTimer(0);
  }, []);

  const handleCheckboxChange = (checked: boolean) => {
    if (checked) {
      setShowDisclaimer(true);
    } else {
      setDeclarationAccepted(false);
      setShowDisclaimer(false);
    }
  };

  const handleDisclaimerAccept = () => {
    setDeclarationAccepted(true);
    setShowDisclaimer(false);
  };

  const handleDisclaimerClose = () => {
    setShowDisclaimer(false);
  };

  /** -------------------- UI -------------------- **/
  return (
    <div className="max-w-4xl mx-auto p-6">
      {/* Header */}
      <div className="text-center mb-8">
        <h2 className="text-2xl font-bold text-gray-900 mb-2">AI Grooming Assessment</h2>
        <p className="text-gray-600 text-sm">Automated liveliness detection and grooming evaluation</p>
      </div>

      {/* Crew Info */}
      <div className="bg-gradient-to-r from-[#000099] to-blue-700 rounded-2xl p-6 mb-8 border border-blue-200">
        <div className="flex justify-center items-center gap-6">
          <div className="text-center">
            <div className="text-white font-bold text-lg">{crewName}</div>
            <div className="text-blue-100 text-sm">Crew Member</div>
          </div>
          <div className="w-px h-12 bg-blue-300"></div>
          <div className="text-center">
            <div className="text-white font-bold text-lg">{igaCode}</div>
            <div className="text-blue-100 text-sm">IGA Code</div>
          </div>
        </div>
      </div>
      {/* Select Base */}
      <div className="mb-6">
        <label htmlFor="base-select" className="block text-sm font-semibold text-gray-700 mb-2">
          Select Base <span className="text-red-500">*</span>
        </label>
        <select
          id="base-select"
          value={selectedBase}
          onChange={(e) => setSelectedBase(e.target.value)}
          disabled={status === "detecting" || status === "recording" || status === "uploading"}
          className="w-full px-4 py-3 border-2 border-gray-300 rounded-xl focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500 transition-all disabled:opacity-50 disabled:cursor-not-allowed bg-white text-gray-800 font-medium"
        >
          <option value="">-- Select Your Base Location --</option>
          {bases.map((base) => (
            <option key={base} value={base}>{base}</option>
          ))}
        </select>
        {!selectedBase && status === "ready" && (
          <p className="text-sm text-amber-600 mt-2 flex items-center gap-2">
            <svg className="w-4 h-4" fill="currentColor" viewBox="0 0 20 20">
              <path fillRule="evenodd" d="M8.257 3.099c.765-1.36 2.722-1.36 3.486 0l5.58 9.92c.75 1.334-.213 2.98-1.742 2.98H4.42c-1.53 0-2.493-1.646-1.743-2.98l5.58-9.92zM11 13a1 1 0 11-2 0 1 1 0 012 0zm-1-8a1 1 0 00-1 1v3a1 1 0 002 0V6a1 1 0 00-1-1z" clipRule="evenodd" />
            </svg>
            Please select your base location before proceeding
          </p>
        )}
      </div>

      {/* Declaration */}
      <div className="bg-white rounded-2xl shadow-lg border border-gray-200 p-6 mb-8">
        <div className="flex items-start gap-4">
          <div className="flex-shrink-0 mt-1">
            <label className="relative inline-flex items-center cursor-pointer">
              <input
                type="checkbox"
                checked={declarationAccepted}
                onChange={(e) => handleCheckboxChange(e.target.checked)}
                className="sr-only peer"
              />
              <div className="relative w-5 h-5 bg-gray-200 peer-focus:outline-none peer-focus:ring-4 peer-focus:ring-blue-300 rounded border-2 border-gray-300 peer-checked:bg-[#000099] peer-checked:border-[#000099] transition-all duration-300">
                <div
                  className={`absolute inset-0 flex items-center justify-center transition-all duration-300 ${declarationAccepted ? 'opacity-100' : 'opacity-0'
                    }`}
                >
                  <svg className="w-3 h-3 text-white" fill="currentColor" viewBox="0 0 20 20">
                    <path
                      fillRule="evenodd"
                      d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z"
                      clipRule="evenodd"
                    />
                  </svg>
                </div>
              </div>
            </label>
          </div>
          <div className="flex-1">
            <p className="text-gray-800 text-sm leading-relaxed">
              I hereby declare that all the information provided is correct and accurate. I understand that this
              assessment will be used for grooming compliance evaluation and I consent to the use of camera for this
              purpose.
            </p>
          </div>
        </div>
      </div>

      {/* Camera surface (visible whenever we're using the camera) */}
      {status !== 'uploading' && !groomingResult && (
        <div className="bg-white rounded-2xl shadow-xl border border-gray-200 overflow-hidden mb-8">
          <div className="relative">
            <video ref={videoRef} autoPlay playsInline muted className="w-full h-auto bg-black max-h-96 object-cover" />
            {/* Liveliness overlay during detecting/recording */}
            {(status === 'detecting' || status === 'recording') && (
              <div className="absolute top-4 left-4 right-4">
                <div className="bg-black bg-opacity-50 backdrop-blur-sm rounded-xl p-4">
                  <p className="feedback-overlay text-center text-white">
                    <span style={{ color: actionsDoneRef.current?.blink ? '#16a34a' : '#ffffff' }}>
                      Blink {actionsDoneRef.current?.blink ? '✓' : ''}
                    </span>
                    {status === 'recording' && (
                      <span className="ml-3 inline-block rounded-full bg-blue-600 px-2 py-0.5 text-xs">
                        {recTimer}s / 10s
                      </span>
                    )}
                  </p>
                </div>
              </div>
            )}
          </div>

          {/* Feedback bar */}
          <div className="bg-gray-50 p-4 border-t">
            <div className="text-center">
              <p className="text-gray-700 text-sm font-medium">{feedback}</p>
            </div>
          </div>
        </div>
      )}

      {/* Controls */}
      <div className="flex flex-col items-center gap-6 mb-8">
        {status === 'ready' && (
          <div className="flex flex-col items-center gap-4">
            <div className="flex flex-wrap justify-center gap-4">
              <button
                onClick={goToLiveOptions}
                disabled={!crewName || !selectedBase || !igaCode || !declarationAccepted}
                className={`px-8 py-4 rounded-xl text-sm font-semibold transition-all duration-300 transform hover:scale-105 ${declarationAccepted
                    ? 'bg-gradient-to-r from-[#000099] to-blue-700 text-white hover:shadow-lg'
                    : 'bg-gray-300 text-gray-500 cursor-not-allowed'
                  }`}
              >
                Do Live Check
              </button>

              {declarationAccepted && onUploadVideo && (
                <button
                  onClick={onUploadVideo}
                  className="bg-gradient-to-r from-blue-700 to-blue-600 text-white px-8 py-4 rounded-xl text-sm font-semibold hover:shadow-lg transition-all duration-300 transform hover:scale-105"
                >
                  Upload Existing Video
                </button>
              )}
            </div>
          </div>
        )}

        {status === 'chooseLive' && (
          <div className="flex flex-col items-center gap-6">
            <p className="text-gray-700 text-center font-medium">Choose your live assessment method:</p>
            <div className="flex flex-wrap justify-center gap-4">
              <button
                onClick={startPhotoFlow}
                disabled={!declarationAccepted || !selectedBase}
                className={`px-6 py-3 rounded-xl text-sm font-semibold transition-all duration-300 ${declarationAccepted
                    ? 'bg-gradient-to-r from-green-600 to-green-500 text-white hover:shadow-lg transform hover:scale-105'
                    : 'bg-gray-300 text-gray-500 cursor-not-allowed'
                  }`}
              >
                Photo (with Liveliness)
              </button>
              <button
                onClick={startVideoFlow}
                disabled={!declarationAccepted}
                className={`px-6 py-3 rounded-xl text-sm font-semibold transition-all duration-300 ${declarationAccepted
                    ? 'bg-gradient-to-r from-[#000099] to-blue-700 text-white hover:shadow-lg transform hover:scale-105'
                    : 'bg-gray-300 text-gray-500 cursor-not-allowed'
                  }`}
              >
                Live Video (10s + Liveliness)
              </button>
            </div>
          </div>
        )}

        {(status === 'detecting' || status === 'recording') && (
          <div className="flex flex-wrap justify-center gap-4">
            {status === 'recording' && (
              <button
                onClick={stopRecording}
                className="bg-gradient-to-r from-amber-600 to-amber-500 text-white px-6 py-3 rounded-xl text-sm font-semibold hover:shadow-lg transition-all duration-300 transform hover:scale-105"
              >
                Stop Recording ({recTimer}s)
              </button>
            )}
            <button
              onClick={handleCancel}
              className="bg-gradient-to-r from-red-600 to-red-500 text-white px-6 py-3 rounded-xl text-sm font-semibold hover:shadow-lg transition-all duration-300 transform hover:scale-105"
            >
              Cancel
            </button>
          </div>
        )}
      </div>

      {/* Processing */}
      {status === 'uploading' && (
        <div className="bg-white rounded-2xl shadow-xl border border-gray-200 p-12 text-center">
          <div className="flex flex-col items-center gap-6">
            <div className="relative">
              <div className="w-16 h-16 border-4 border-blue-200 rounded-full animate-spin border-t-[#000099]"></div>
              <div className="absolute inset-0 flex items-center justify-center">
                <div className="w-8 h-8 bg-[#000099] rounded-full animate-pulse"></div>
              </div>
            </div>
            <div>
              <p className="text-gray-900 font-semibold text-lg mb-2">Analyzing Your Assessment</p>
              <p className="text-gray-600 text-sm">Our AI is evaluating your grooming standards...</p>
            </div>
          </div>
        </div>
      )}

      {/* Results */}
      {status === 'done' && groomingResult && (
        <div className="bg-white rounded-2xl shadow-xl border border-gray-200 p-8">
          <div className="text-center mb-8">
            <h3 className="text-2xl font-bold text-gray-900 mb-4">Assessment Complete</h3>
            <div className="flex justify-center items-center gap-6 mb-6">
              <div
                className={`px-6 py-3 rounded-full text-white font-bold text-lg ${groomingResult.assessment === 'COMPLIANT'
                    ? 'bg-gradient-to-r from-green-600 to-green-500'
                    : 'bg-gradient-to-r from-red-600 to-red-500'
                  }`}
              >
                {groomingResult.assessment}
              </div>
              <div className="text-center">
                <div className="text-4xl font-bold text-[#000099]">{groomingResult.score ?? 0}/10</div>
                <div className="text-gray-600 text-sm">Overall Score</div>
              </div>
            </div>
          </div>

          {/* Detailed Scores */}
          {groomingResult.scores && (
            <div className="mb-8">
              <h4 className="text-lg font-semibold text-gray-900 mb-4 text-center">Category Breakdown</h4>
              <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
                {[
                  { key: 'uniform', label: 'Uniform', max: 3 },
                  { key: 'hairstyle', label: 'Hairstyle', max: 2 },
                  { key: 'makeup', label: 'Makeup', max: 2 },
                  { key: 'nails', label: 'Nails', max: 1 },
                  { key: 'accessories', label: 'Accessories', max: 2 },
                ].map((item) => {
                  const score = groomingResult.scores![item.key as keyof typeof groomingResult.scores];
                  const percentage = Math.round((score / item.max) * 100);
                  return (
                    <div key={item.key} className="bg-blue-50 rounded-xl p-4 text-center border border-blue-200">
                      <div className="text-2xl font-bold text-[#000099] mb-1">
                        {score}/{item.max}
                      </div>
                      <div className="text-gray-600 text-sm mb-2">{item.label}</div>
                      <div
                        className={`text-xs font-medium ${percentage >= 80 ? 'text-green-600' : percentage >= 60 ? 'text-yellow-600' : 'text-red-600'
                          }`}
                      >
                        {percentage}%
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          {/* Issues */}
          {groomingResult.issues && groomingResult.issues.length > 0 && (
            <div className="bg-red-50 rounded-xl p-6 border border-red-200 mb-6">
              <h4 className="text-red-800 font-semibold text-lg mb-4">Issues Identified</h4>
              <ul className="space-y-2">
                {groomingResult.issues.map((issue, i) => (
                  <li key={i} className="flex items-start gap-3 text-red-700">
                    <span className="bg-red-200 text-red-800 rounded-full w-6 h-6 flex items-center justify-center text-xs font-bold flex-shrink-0 mt-0.5">
                      {i + 1}
                    </span>
                    <span className="text-sm">{issue}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {/* Recommendations */}
          {groomingResult.recommendations && groomingResult.recommendations.length > 0 && (
            <div className="bg-green-50 rounded-xl p-6 border border-green-200 mb-6">
              <h4 className="text-green-800 font-semibold text-lg mb-4">Recommendations</h4>
              <ul className="space-y-2">
                {groomingResult.recommendations.map((rec, i) => (
                  <li key={i} className="flex items-start gap-3 text-green-700">
                    <span className="bg-green-200 text-green-800 rounded-full w-6 h-6 flex items-center justify-center text-xs font-bold flex-shrink-0 mt-0.5">
                      {i + 1}
                    </span>
                    <span className="text-sm">{rec}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {/* Actions */}
          <div className="flex justify-center gap-4 mt-8">
            <button
              onClick={handleNewAssessment}
              className="bg-[#000099] text-white px-6 py-3 rounded-xl text-sm font-semibold hover:bg-blue-800 transition-colors duration-300"
            >
              New Assessment
            </button>
            <button
              onClick={handleBackToDashboard}
              className="bg-gray-600 text-white px-6 py-3 rounded-xl text-sm font-semibold hover:bg-gray-700 transition-colors duration-300"
            >
              Back to Dashboard
            </button>
          </div>
        </div>
      )}

      {/* Error */}
      {error && (
        <div className="bg-red-50 border border-red-200 rounded-2xl p-6 text-center">
          <h3 className="text-red-800 font-semibold text-lg mb-2">Assessment Error</h3>
          <p className="text-red-700 text-sm mb-4">{error}</p>
          <div className="flex justify-center gap-4">
            <button
              onClick={handleNewAssessment}
              className="bg-red-600 text-white px-6 py-3 rounded-xl text-sm font-semibold hover:bg-red-700 transition-colors duration-300"
            >
              Try Again
            </button>
            <button
              onClick={handleCancel}
              className="bg-gray-600 text-white px-6 py-3 rounded-xl text-sm font-semibold hover:bg-gray-700 transition-colors duration-300"
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {/* Disclaimer Modal */}
      {showDisclaimer && (
        <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-2xl shadow-2xl max-w-4xl max-h-[90vh] overflow-y-auto">
            <div className="bg-gradient-to-r from-[#000099] to-blue-700 text-white p-6 rounded-t-2xl">
              <h3 className="text-2xl font-bold text-center">GROOMING ASSESSMENT TOOL - DEMO DISCLAIMER</h3>
            </div>

            <div className="p-6 space-y-6">
              <div className="bg-amber-50 border-l-4 border-amber-500 p-4 rounded">
                <div className="flex items-start gap-3">
                  <span className="text-2xl">⚠️</span>
                  <div>
                    <h4 className="font-bold text-amber-900 mb-2">IMPORTANT NOTICE</h4>
                    <p className="text-amber-800 text-sm">This is a demonstration tool powered by AI. Results are generated automatically and may contain inaccuracies. Always rely on trained human inspectors for final grooming compliance decisions.</p>
                  </div>
                </div>
              </div>

              <div>
                <h4 className="font-bold text-[#000099] text-lg mb-3">KNOWN LIMITATIONS & ENVIRONMENTAL FACTORS</h4>
                <div className="space-y-3">
                  <div className="bg-blue-50 p-3 rounded-lg">
                    <h5 className="font-semibold text-gray-900 mb-1">Lighting Conditions</h5>
                    <p className="text-gray-700 text-sm">Poor, dim, or colored lighting can affect accuracy of color detection (hair, makeup, nail polish). Bright backlighting may obscure details.</p>
                  </div>
                  <div className="bg-blue-50 p-3 rounded-lg">
                    <h5 className="font-semibold text-gray-900 mb-1">Camera Angle & Resolution</h5>
                    <p className="text-gray-700 text-sm">Extreme angles, blurry footage, or low resolution may prevent accurate assessment of nails, stockings, or badge positioning.</p>
                  </div>
                  <div className="bg-blue-50 p-3 rounded-lg">
                    <h5 className="font-semibold text-gray-900 mb-1">Video Quality</h5>
                    <p className="text-gray-700 text-sm">Compression artifacts, motion blur, or low frame rate may cause misidentification of hairstyle, scarf knots, or accessory compliance.</p>
                  </div>
                  <div className="bg-blue-50 p-3 rounded-lg">
                    <h5 className="font-semibold text-gray-900 mb-1">Partial Visibility</h5>
                    <p className="text-gray-700 text-sm">If only upper body is visible, stockings, lower portions of uniform, and certain accessories cannot be assessed. These are marked (NOT VISIBLE).</p>
                  </div>
                  <div className="bg-blue-50 p-3 rounded-lg">
                    <h5 className="font-semibold text-gray-900 mb-1">Makeup Variation</h5>
                    <p className="text-gray-700 text-sm">Makeup appearance varies significantly under different lighting. The tool may misclassify shades or blending under inconsistent conditions.</p>
                  </div>
                </div>
              </div>

              <div className="grid md:grid-cols-2 gap-4">
                <div>
                  <h4 className="font-bold text-[#000099] text-lg mb-3">WHAT THE TOOL ASSESSES</h4>
                  <ul className="space-y-2 text-sm text-gray-700">
                    <li className="flex items-start gap-2">
                      <span className="text-green-600 mt-0.5">✓</span>
                      <span>Hairstyle (style, neatness, color, integrity)</span>
                    </li>
                    <li className="flex items-start gap-2">
                      <span className="text-green-600 mt-0.5">✓</span>
                      <span>Makeup (base, eyes, lips, overall finish)</span>
                    </li>
                    <li className="flex items-start gap-2">
                      <span className="text-green-600 mt-0.5">✓</span>
                      <span>Nails (length, shape, color, finish)</span>
                    </li>
                    <li className="flex items-start gap-2">
                      <span className="text-green-600 mt-0.5">✓</span>
                      <span>Accessories (rings, earrings, watch, prohibitions)</span>
                    </li>
                    <li className="flex items-start gap-2">
                      <span className="text-green-600 mt-0.5">✓</span>
                      <span>Uniform (tunic, scarf, name badge, stockings)</span>
                    </li>
                  </ul>
                </div>

                <div>
                  <h4 className="font-bold text-[#000099] text-lg mb-3">WHAT THE TOOL DOES NOT DO</h4>
                  <ul className="space-y-2 text-sm text-gray-700">
                    <li className="flex items-start gap-2">
                      <span className="text-red-600 mt-0.5">✗</span>
                      <span>Does not verify identity or authenticity</span>
                    </li>
                    <li className="flex items-start gap-2">
                      <span className="text-red-600 mt-0.5">✗</span>
                      <span>Does not assess behavior or conduct</span>
                    </li>
                    <li className="flex items-start gap-2">
                      <span className="text-red-600 mt-0.5">✗</span>
                      <span>Does not detect subtle violations requiring expert judgment</span>
                    </li>
                    <li className="flex items-start gap-2">
                      <span className="text-red-600 mt-0.5">✗</span>
                      <span>Cannot assess items not visible in frame</span>
                    </li>
                  </ul>
                </div>
              </div>

              <div>
                <h4 className="font-bold text-[#000099] text-lg mb-3">BEST PRACTICES FOR ACCURATE RESULTS</h4>
                <ul className="space-y-2 text-sm text-gray-700">
                  <li className="flex items-start gap-2">
                    <span className="text-blue-600 mt-0.5">•</span>
                    <span>Use well-lit environments with natural or neutral lighting</span>
                  </li>
                  <li className="flex items-start gap-2">
                    <span className="text-blue-600 mt-0.5">•</span>
                    <span>Ensure full-body or at least upper-body visibility</span>
                  </li>
                  <li className="flex items-start gap-2">
                    <span className="text-blue-600 mt-0.5">•</span>
                    <span>Provide clear, high-resolution images or videos (minimum 480p)</span>
                  </li>
                  <li className="flex items-start gap-2">
                    <span className="text-blue-600 mt-0.5">•</span>
                    <span>Keep camera steady and avoid extreme angles</span>
                  </li>
                  <li className="flex items-start gap-2">
                    <span className="text-blue-600 mt-0.5">•</span>
                    <span>Use consistent white balance</span>
                  </li>
                </ul>
              </div>

              <div className="bg-blue-50 border-l-4 border-[#000099] p-4 rounded">
                <h4 className="font-bold text-[#000099] mb-2">AI-Generated Results Disclaimer</h4>
                <p className="text-sm text-gray-700">AI-generated results may not always be accurate. This is a demonstration system.</p>
              </div>
            </div>

            <div className="bg-gray-50 p-6 rounded-b-2xl flex justify-center gap-4">
              <button
                onClick={handleDisclaimerAccept}
                className="bg-gradient-to-r from-[#000099] to-blue-700 text-white px-8 py-3 rounded-xl text-sm font-semibold hover:shadow-lg transition-all duration-300"
              >
                I Understand & Accept
              </button>
              <button
                onClick={handleDisclaimerClose}
                className="bg-gray-600 text-white px-8 py-3 rounded-xl text-sm font-semibold hover:bg-gray-700 transition-colors duration-300"
              >
                Cancel
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

export default UnifiedCheck;
