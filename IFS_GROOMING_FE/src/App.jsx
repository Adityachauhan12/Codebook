import React, { useState, useRef } from 'react';
import UnifiedCheck from './components/Unified_Check';
import UploadVideo from './components/UploadVideo';
import './App.css';

function App() {
  const [crewName, setCrewName] = useState('');
  const [igaCode, setIgaCode] = useState('');
  const [showUnifiedCheck, setShowUnifiedCheck] = useState(false);
  const [checkDone, setCheckDone] = useState(false);
  const unifiedCheckRef = useRef(null);

  const isCrewInfoFilled = crewName.trim() !== '' && igaCode.trim() !== '';

  const handleRunCheck = () => {
    setShowUnifiedCheck(false); // Reset component
    setCheckDone(false); // Reset completion status

    setTimeout(() => {
      setShowUnifiedCheck(true);

      setTimeout(() => {
        const testSection = document.getElementById('test-section');
        if (testSection) {
          testSection.scrollIntoView({ behavior: 'smooth' });
        }

        if (unifiedCheckRef.current) {
          unifiedCheckRef.current.startUnifiedCheck();
        }
      }, 300);
    }, 100);
  };

  return (
    <div className="app-container">
     <header className="navbar" style={{ position: 'relative', top: -35, marginBottom: '12px' }}>
        <div className="logo">
          <img src="/IFS_logo.png" alt="IFS Logo" className="indigo-logo" style={{ width: '90px' }} />
          <h1 style={{ marginTop: '-6px' }}>IndiGo Grooming Assistant</h1>
        </div>
        <img src="/indigo_logo.avif" alt="IndiGo Logo" style={{ height: '60px', borderRadius: '8px' }} />
      </header>

      <div className="crew-info-form stylish-form">
        <h2>Crew Identification</h2>
        <div className="input-pair" style={{ width: '90%' }}>
          <div className="input-box" style={{ flex: '1 1 55%', minWidth: '260px' }}>
            <label htmlFor="crewName">Crew Name</label>
            <input
              id="crewName"
              type="text"
              placeholder="e.g. Priya Sharma"
              value={crewName}
              onChange={(e) => setCrewName(e.target.value)}
            />
          </div>
          <div className="input-box" style={{ flex: '1 1 45%', minWidth: '260px' }}>
            <label htmlFor="igaCode">IGA Code</label>
            <input
              id="igaCode"
              type="text"
              placeholder="e.g. IGA5678"
              value={igaCode}
              onChange={(e) => setIgaCode(e.target.value)}
            />
          </div>
        </div>
      </div>

      <main className="main-content">
        <div className="button-section">
          <button
            className="ready-button"
            onClick={handleRunCheck}
            disabled={!isCrewInfoFilled}
          >
            Run Grooming + Liveliness
          </button>
        </div>

        {showUnifiedCheck && (
          <UnifiedCheck
            ref={unifiedCheckRef}
            crewName={crewName}
            igaCode={igaCode}
            onComplete={() => setCheckDone(true)}
          />
        )}

        <UploadVideo crewName={crewName} igaCode={igaCode} />
      </main>

      <footer style={{ textAlign: 'center', marginTop: '40px', fontSize: '0.85rem', color: '#666' }}>
        Powered by IndiGo IFS • Experimental Assistant • For support, contact your IFS Partner
      </footer>
    </div>
  );
}

export default App;