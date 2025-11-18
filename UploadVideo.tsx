import React, { useState, useRef } from 'react';
import { UploadVideoProps, GroomingAssessmentResult } from '../types';
import { getApiUrl } from '../utils/groomingHelpers';

const UploadVideo: React.FC<UploadVideoProps> = ({ 
  crewName, 
  igaCode, 
  onComplete,
  onError
}) => {
  const [result, setResult] = useState<GroomingAssessmentResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [uploadProgress, setUploadProgress] = useState(0);
  const [dragOver, setDragOver] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const handleVideoUpload = async (file: File) => {
    if (!file) return;

    // Validate file type
    if (!file.type.startsWith('video/')) {
      const errorMsg = 'Please select a valid video file';
      setError(errorMsg);
      if (onError) onError(errorMsg);
      return;
    }

    // Validate file size (max 50MB)
    const maxSize = 50 * 1024 * 1024;
    if (file.size > maxSize) {
      const errorMsg = 'Video file size should be less than 50MB';
      setError(errorMsg);
      if (onError) onError(errorMsg);
      return;
    }

    const formData = new FormData();
    formData.append('video', file);
    formData.append('name', crewName);
    formData.append('iga_code', igaCode);

    setError(null);
    setResult(null);
    setLoading(true);
    setUploadProgress(0);

    try {
      const xhr = new XMLHttpRequest();
      
      // Track upload progress
      xhr.upload.addEventListener('progress', (e) => {
        if (e.lengthComputable) {
          const percentComplete = (e.loaded / e.total) * 100;
          setUploadProgress(percentComplete);
        }
      });

      const response = await new Promise<Response>((resolve, reject) => {
        xhr.onload = () => {
          if (xhr.status >= 200 && xhr.status < 300) {
            resolve(new Response(xhr.response, {
              status: xhr.status,
              statusText: xhr.statusText
            }));
          } else {
            reject(new Error(`HTTP ${xhr.status}: ${xhr.statusText}`));
          }
        };
        
        xhr.onerror = () => reject(new Error('Network error occurred'));
        xhr.ontimeout = () => reject(new Error('Request timeout'));
        
        xhr.open('POST', getApiUrl('/check-grooming-video'));
        xhr.timeout = 120000; // 2 minutes timeout
        xhr.send(formData);
      });

      if (!response.ok) {
        const errorText = await response.text();
        throw new Error(`HTTP ${response.status}: ${errorText || 'Unknown error'}`);
      }

      const data = await response.json();
      
      if (!data || data.status !== 'ok') {
        throw new Error(data?.error || 'Invalid response from server');
      }

      const assessmentResult = data.result;
      setResult(assessmentResult);
      
      // DON'T call onComplete here - let user choose when to go back
      
    } catch (err) {
      console.error('Video upload error:', err);
      const errorMsg = err instanceof Error ? err.message : 'Video upload failed';
      setError(errorMsg);
      if (onError) {
        onError(errorMsg);
      }
    } finally {
      setLoading(false);
      setUploadProgress(0);
    }
  };

  const handleFileSelect = (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (file) {
      handleVideoUpload(file);
    }
  };

  const handleDrop = (event: React.DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    setDragOver(false);
    
    const file = event.dataTransfer.files?.[0];
    if (file) {
      handleVideoUpload(file);
    }
  };

  const handleDragOver = (event: React.DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    setDragOver(true);
  };

  const handleDragLeave = () => {
    setDragOver(false);
  };

  // NEW: Handle back to dashboard with results
  const handleBackToDashboard = () => {
    if (onComplete && result) {
      onComplete(result);
    }
  };

  const handleNewUpload = () => {
    setResult(null);
    setError(null);
    setUploadProgress(0);
  };

  return (
    <div className="max-w-4xl mx-auto p-6">
      {/* Header */}
      <div className="text-center mb-8">
        <h2 className="text-2xl font-bold text-gray-900 mb-2">
          Video Grooming Assessment
        </h2>
        <p className="text-gray-600 text-sm">
          Upload a video for AI-powered grooming evaluation
        </p>
      </div>

      {/* Crew Information Card */}
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

      {/* Hidden file input */}
      <input
        type="file"
        accept="video/*"
        ref={fileInputRef}
        onChange={handleFileSelect}
        className="hidden"
      />

      {/* Upload Area */}
      {!loading && !result && !error && (
        <div className="bg-white rounded-2xl shadow-xl border border-gray-200 overflow-hidden mb-8">
          <div
            className={`p-12 text-center transition-all duration-300 cursor-pointer ${
              dragOver
                ? 'border-2 border-dashed border-[#000099] bg-blue-50'
                : 'border-2 border-dashed border-gray-300 hover:border-[#000099] hover:bg-blue-50'
            }`}
            onDrop={handleDrop}
            onDragOver={handleDragOver}
            onDragLeave={handleDragLeave}
            onClick={() => fileInputRef.current?.click()}
          >
            <div className="flex flex-col items-center gap-6">
              {/* Upload Icon */}
              <div className="text-6xl">
                {dragOver ? '' : ''}
              </div>

              {/* Upload Text */}
              <div>
                <h3 className="text-xl font-bold text-gray-900 mb-3">
                  {dragOver ? 'Drop your video here' : 'Upload Grooming Video'}
                </h3>
                <p className="text-gray-600 text-sm mb-4">
                  Drag and drop a video file here, or click to browse
                </p>
                <div className="flex flex-wrap justify-center gap-4 text-xs text-gray-500">
                  <span className="flex items-center gap-1">
                    <span>Format:</span> MP4, WebM, AVI, MOV
                  </span>
                  <span className="flex items-center gap-1">
                    <span></span> Max size: 50MB
                  </span>
                  <span className="flex items-center gap-1">
                    <span></span> Duration: Up to 2 minutes
                  </span>
                </div>
              </div>

              {/* Upload Button */}
              <button
                className="bg-gradient-to-r from-[#000099] to-blue-700 text-white px-8 py-3 rounded-xl text-sm font-semibold hover:shadow-lg transition-all duration-300 transform hover:scale-105"
                onClick={(e) => {
                  e.stopPropagation();
                  fileInputRef.current?.click();
                }}
              >
                <span className="flex items-center gap-2">
                  <span></span>
                  Choose Video File
                </span>
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Loading State */}
      {loading && (
        <div className="bg-white rounded-2xl shadow-xl border border-gray-200 p-12 text-center mb-8">
          <div className="flex flex-col items-center gap-6">
            <div className="relative">
              <div className="w-16 h-16 border-4 border-blue-200 rounded-full animate-spin border-t-[#000099]"></div>
              <div className="absolute inset-0 flex items-center justify-center">
                <div className="w-8 h-8 bg-[#000099] rounded-full animate-pulse"></div>
              </div>
            </div>
            <div>
              <p className="text-gray-900 font-semibold text-lg mb-2">
                Uploading Video
              </p>
              <p className="text-gray-600 text-sm mb-4">
                Please wait while we process your video...
              </p>
              {uploadProgress > 0 && (
              <div className="w-full bg-gray-200 rounded-full h-2 mb-2">
               <div
                className={`bg-gradient-to-r from-[#000099] to-blue-700 h-2 rounded-full transition-all duration-300 w-[${uploadProgress}%]`}
                ></div>
              </div>
              )}
              {uploadProgress > 0 && (
                <p className="text-xs text-gray-500">
                  {Math.round(uploadProgress)}% complete
                </p>
              )}
            </div>
          </div>
        </div>
      )}

      {/* Error Display */}
      {error && (
        <div className="bg-red-50 border border-red-200 rounded-2xl p-8 text-center mb-8">
          <h3 className="text-red-800 font-semibold text-lg mb-2">Upload Error</h3>
          <p className="text-red-700 text-sm mb-4">{error}</p>
          <div className="flex justify-center gap-4">
            <button
              onClick={() => {
                setError(null);
                fileInputRef.current?.click();
              }}
              className="bg-red-600 text-white px-6 py-3 rounded-xl text-sm font-semibold hover:bg-red-700 transition-colors duration-300"
            >
              Try Again
            </button>
          </div>
        </div>
      )}

      {/* Results Display - Complete with all sections */}
      {result && (
        <div className="bg-white rounded-2xl shadow-xl border border-gray-200 p-8">
          <div className="text-center mb-8">
            <h3 className="text-2xl font-bold text-gray-900 mb-4">
              Assessment Complete
            </h3>

            <div className="flex justify-center items-center gap-6 mb-6">
              <div className={`px-6 py-3 rounded-full text-white font-bold text-lg ${
                result.assessment === "COMPLIANT"
                  ? "bg-gradient-to-r from-green-600 to-green-500"
                  : "bg-gradient-to-r from-red-600 to-red-500"
              }`}>
                {result.assessment}
              </div>
              <div className="text-center">
                <div className="text-4xl font-bold text-[#000099]">
                  {result.score ?? 0}/10
                </div>
                <div className="text-gray-600 text-sm">Overall Score</div>
              </div>
            </div>
          </div>

          {/* Detailed Scores */}
          {result.scores && (
            <div className="mb-8">
              <h4 className="text-lg font-semibold text-gray-900 mb-4 text-center">
                Category Breakdown
              </h4>
              <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
                {[
                  { key: "uniform", label: "Uniform", max: 3 },
                  { key: "hairstyle", label: "Hairstyle", max: 2 },
                  { key: "makeup", label: "Makeup", max: 2 },
                  { key: "nails", label: "Nails", max: 1 },
                  { key: "accessories", label: "Accessories", max: 2 }
                ].map((item) => {
                  const score = result.scores![item.key as keyof typeof result.scores];
                  const percentage = Math.round((score / item.max) * 100);

                  return (
                    <div key={item.key} className="bg-blue-50 rounded-xl p-4 text-center border border-blue-200">
                      <div className="text-2xl font-bold text-[#000099] mb-1">
                        {score}/{item.max}
                      </div>
                      <div className="text-gray-600 text-sm mb-2">{item.label}</div>
                      <div className={`text-xs font-medium ${
                        percentage >= 80 ? 'text-green-600' :
                        percentage >= 60 ? 'text-yellow-600' : 'text-red-600'
                      }`}>
                        {percentage}%
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          {/* Issues */}
          {result.issues && result.issues.length > 0 && (
            <div className="bg-red-50 rounded-xl p-6 border border-red-200 mb-6">
              <h4 className="text-red-800 font-semibold text-lg mb-4">
                Issues Identified
              </h4>
              <ul className="space-y-2">
                {result.issues.map((issue, i) => (
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
          {result.recommendations && result.recommendations.length > 0 && (
            <div className="bg-green-50 rounded-xl p-6 border border-green-200 mb-6">
              <h4 className="text-green-800 font-semibold text-lg mb-4">
                Recommendations
              </h4>
              <ul className="space-y-2">
                {result.recommendations.map((rec, i) => (
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

          {/* Action Buttons */}
          <div className="flex justify-center gap-4 mt-8">
            <button
              onClick={handleNewUpload}
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
    </div>
  );
};

export default UploadVideo;
