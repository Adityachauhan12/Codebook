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
            {result?.person?.name || '-'} • IGA: {result?.person?.iga_code || '-'}
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
              <div style={{ fontWeight: 600 }}>Uniform</div>
              <div>{result.scores?.uniform ?? '-'}/3</div>

              <div style={{ fontWeight: 600 }}>Nails</div>
              <div>{result.scores?.nails ?? '-'}/1</div>

              <div style={{ fontWeight: 600 }}>Hairstyle</div>
              <div>{result.scores?.hairstyle ?? '-'}/2</div>

              <div style={{ fontWeight: 600 }}>Makeup</div>
              <div>{result.scores?.makeup ?? '-'}/2</div>

              <div style={{ fontWeight: 600 }}>Accessories</div>
              <div>{result.scores?.accessories ?? '-'}/2</div>
            </div>
          </div>

          <div style={{ marginTop: 12 }}>
            <h4>Details</h4>
            <div
              style={{
                display: 'grid',
                gridTemplateColumns: '150px 1fr',
                rowGap: 6,
                columnGap: 12,
              }}
            >
              <div style={{ fontWeight: 600 }}>Uniform</div>
              <div>{result.details?.uniform || '-'}</div>

              <div style={{ fontWeight: 600 }}>Hairstyle</div>
              <div>{result.details?.hairstyle || '-'}</div>

              <div style={{ fontWeight: 600 }}>Makeup</div>
              <div>{result.details?.makeup || '-'}</div>

              <div style={{ fontWeight: 600 }}>Nails</div>
              <div>{result.details?.nails || '-'}</div>

              <div style={{ fontWeight: 600 }}>Accessories</div>
              <div>{result.details?.accessories || '-'}</div>
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
