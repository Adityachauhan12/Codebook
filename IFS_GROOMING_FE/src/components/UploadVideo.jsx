import React, { useState } from 'react';

/**
 * UploadVideo component allows users to upload a video file
 * for grooming verification. It sends the video to the backend
 * and displays the result.
 *
 * Props:
 * - crewName: string - Name of the crew member
 * - igaCode: string - IGA code of the crew member
 */
function UploadVideo({ crewName, igaCode }) {
  const [response, setResponse] = useState(null);
  const [loading, setLoading] = useState(false);

  const BASE_URL = import.meta.env.VITE_API_BASE_URL;

  /**
   * Handles video file selection and uploads it to the backend.
   * Displays grooming result after processing.
   */
  const handleVideoUpload = (event) => {
    const file = event.target.files[0];
    if (!file) return;

    const formData = new FormData();
    formData.append('video', file);
    formData.append('name', crewName);
    formData.append('iga_code', igaCode);

    setLoading(true);

    fetch(`${BASE_URL}/check-grooming-video`, {
      method: 'POST',
      body: formData,
    })
      .then((res) => res.json())
      .then((data) => {
        setResponse(data.result);
        setLoading(false);
      })
      .catch((err) => {
        console.error('Video Upload Error:', err);
        setLoading(false);
      });
  };

  return (
    <div className="upload-section">
      <label className="upload-label">
        <span className="upload-button">Choose Video</span>
        <input
          type="file"
          accept="video/*"
          onChange={handleVideoUpload}
          hidden
        />
      </label>

      {loading && (
        <p className="loader">⏳ Uploading and checking video...</p>
      )}

      {response && (
        <div className="result-card">
          <h3>Assessment Result</h3>
          <pre className="result-text">{response}</pre>
        </div>
      )}
    </div>
  );
}

export default UploadVideo;