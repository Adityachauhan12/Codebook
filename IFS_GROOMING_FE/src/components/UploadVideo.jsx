// src/components/UploadVideo.jsx
import React, { useState } from 'react';

function UploadVideo({ crewName, igaCode }) {
  const [result, setResult] = useState(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState(null);
  const BASE_URL = import.meta.env.VITE_API_BASE_URL;

  const handleVideoUpload = (event) => {
    const file = event.target.files[0];
    if (!file) return;
    const formData = new FormData();
    formData.append('video', file);
    formData.append('name', crewName);
    formData.append('iga_code', igaCode);

    setErr(null);
    setResult(null);
    setLoading(true);

    fetch(`${BASE_URL}/check-grooming-video`, { method: 'POST', body: formData })
      .then((res) => res.json())
      .then((data) => {
        if (!data || data.status !== 'ok') throw new Error(data?.error || 'API error');
        setResult(data.result);
        setLoading(false);
      })
      .catch((e) => {
        console.error('Video Upload Error:', e);
        setErr('Video grooming check failed.');
        setLoading(false);
      });
  };

  const row = (label, score, max, detail) => (
    <>
      <div style={{ fontWeight: 600 }}>{label}</div>
      <div>
        {score ?? '-'}{max ? `/${max}` : ''}{detail ? ` (${detail})` : ''}
      </div>
    </>
  );

  return (
    <div style={{ marginTop: 16 }}>
      <label style={{ display: 'block', marginBottom: 8 }}>Upload grooming video</label>
      <input type="file" accept="video/*" onChange={handleVideoUpload} />

      {loading && <p style={{ marginTop: 8 }}>⏳ Uploading and checking video...</p>}
      {err && <p style={{ marginTop: 8, color: '#dc2626' }}>{err}</p>}

      {result && (
        <div className="result-card" style={{ marginTop: 12 }}>
          <h3>Grooming Result (Video)</h3>

          <p style={{ margin: '4px 0', color: '#555' }}>
            {result?.person?.name || crewName || '-'} • IGA: {result?.person?.iga_code || igaCode || '-'}
          </p>

          <div style={{ marginBottom: 8 }}>
            <span
              style={{
                padding: '4px 8px',
                borderRadius: 6,
                color: '#fff',
                background: result.assessment === 'COMPLIANT' ? '#16a34a' : '#dc2626',
              }}
            >
              {result.assessment}
            </span>
            <span style={{ marginLeft: 10 }}>Score: {result.score}/10</span>
          </div>

          <div style={{ marginTop: 6 }}>
            <h4>Category Scores</h4>
            <div
              style={{
                display: 'grid',
                gridTemplateColumns: '150px 1fr',
                rowGap: 6,
                columnGap: 12,
              }}
            >
              {row('Uniform',     result.scores?.uniform, 3, result.details?.uniform)}
              {row('Hairstyle',   result.scores?.hairstyle, 2, result.details?.hairstyle)}
              {row('Makeup',      result.scores?.makeup, 2, result.details?.makeup)}
              {row('Nails',       result.scores?.nails, 1, result.details?.nails)}
              {row('Accessories', result.scores?.accessories, 2, result.details?.accessories)}
            </div>
          </div>

          {Array.isArray(result.issues) && result.issues.length > 0 && (
            <>
              <h4 style={{ marginTop: 12 }}>Issues</h4>
              <ul>
                {result.issues.map((it, i) => (
                  <li key={i}>{it}</li>
                ))}
              </ul>
            </>
          )}

          {Array.isArray(result.recommendations) && result.recommendations.length > 0 && (
            <>
              <h4 style={{ marginTop: 12 }}>Recommendations</h4>
              <ul>
                {result.recommendations.map((it, i) => (
                  <li key={i}>{it}</li>
                ))}
              </ul>
            </>
          )}
        </div>
      )}
    </div>
  );
}

export default UploadVideo;
