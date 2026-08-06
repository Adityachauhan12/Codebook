import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  AlertCircle,
  Calendar,
  CheckCircle,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  FileText,
  Loader2,
  RefreshCw,
  Stethoscope,
  Upload,
  X,
} from "lucide-react";
import { useUserData } from "../../hooks/useUserData";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface LeaveAllocation {
  sl_days: number;
  urti_days: number;
  lwp_days: number;
}

interface LeaveRecord {
  leave_id: string;
  crew_name: string;
  iga_code: string;
  base: string;
  start_date: string;
  end_date: string;
  leave_duration_days: number;
  leave_category: string;
  allocation: LeaveAllocation;
  sl_balance: number;
  urti_balance: number;
  medical_issue?: string;
  document_required: boolean;
  document_ids: string[];
  stage: string;
  status: string;
  crew_status: string;
  remarks?: string;
  created_at: string;
  updated_at: string;
}

// ---------------------------------------------------------------------------
// URTI keyword list (mirrors backend _URTI_KEYWORDS)
// ---------------------------------------------------------------------------

const URTI_KEYWORDS = [
  "urti", "respiratory", "upper respiratory", "cold", "cough", "fever",
  "flu", "influenza", "sore throat", "throat", "viral", "virus",
  "infection", "sinusitis", "bronchitis", "rhinitis", "runny nose",
  "congestion", "laryngitis", "pharyngitis", "tonsil",
];

const isUrtiRelated = (text: string): boolean => {
  if (!text) return false;
  const lower = text.toLowerCase();
  return URTI_KEYWORDS.some((kw) => lower.includes(kw));
};

// Local mirror of _smart_crew_allocate for instant preview before API round-trip
const computeAllocationLocal = (
  requestedDays: number,
  slBalance: number,
  urtiBalance: number,
  medicalIssue: string,
): LeaveAllocation => {
  const useUrtiFirst = isUrtiRelated(medicalIssue);

  let remaining = Math.max(requestedDays, 0);

  let slDays = 0;
  let urtiDays = 0;

  if (useUrtiFirst) {
    // URTI → URTI first, then SL
    urtiDays = Math.min(Math.max(urtiBalance, 0), remaining);
    remaining -= urtiDays;

    slDays = Math.min(Math.max(slBalance, 0), remaining);
    remaining -= slDays;
  } else {
    // Non-URTI → SL first, then LWP
    slDays = Math.min(Math.max(slBalance, 0), remaining);
    remaining -= slDays;
  }

  return {
    sl_days: Math.round(slDays * 100) / 100,
    urti_days: Math.round(urtiDays * 100) / 100,
    lwp_days: Math.round(Math.max(remaining, 0) * 100) / 100,
  };
};

const formatDate = (v: string) => {
  if (!v) return "—";
  return new Date(v).toLocaleDateString("en-GB", {
    day: "2-digit", month: "short", year: "numeric",
  });
};

// ---------------------------------------------------------------------------
// Dummy data (shown when backend is unreachable)
// ---------------------------------------------------------------------------

const DUMMY_LEAVES: LeaveRecord[] = [
  {
    leave_id: "URTI-DEMO-001",
    crew_name: "Demo Crew",
    iga_code: "21927",
    base: "DEL",
    start_date: "2026-07-30",
    end_date: "2026-08-01",
    leave_duration_days: 3,
    leave_category: "SL",
    allocation: { sl_days: 0, urti_days: 0, lwp_days: 3 },
    sl_balance: 8,
    urti_balance: 4,
    medical_issue: "",
    document_required: true,
    document_ids: [],
    stage: "CREW",
    status: "PENDING",
    crew_status: "NOT SUBMITTED",
    remarks: "",
    created_at: "2026-07-29T09:00:00.000Z",
    updated_at: "2026-07-29T09:00:00.000Z",
  },
  {
    leave_id: "URTI-DEMO-002",
    crew_name: "Demo Crew",
    iga_code: "21927",
    base: "DEL",
    start_date: "2026-08-04",
    end_date: "2026-08-05",
    leave_duration_days: 2,
    leave_category: "SL",
    allocation: { sl_days: 0, urti_days: 0, lwp_days: 2 },
    sl_balance: 6,
    urti_balance: 2,
    medical_issue: "",
    document_required: false,
    document_ids: [],
    stage: "CREW",
    status: "PENDING",
    crew_status: "NOT SUBMITTED",
    remarks: "",
    created_at: "2026-07-29T10:30:00.000Z",
    updated_at: "2026-07-29T10:30:00.000Z",
  },
];

// ---------------------------------------------------------------------------
// Per-leave action card
// ---------------------------------------------------------------------------

interface LeaveActionCardProps {
  leave: LeaveRecord;
  apiBase: string;
  onSuccess: () => void;
}

const LeaveActionCard: React.FC<LeaveActionCardProps> = ({ leave, apiBase, onSuccess }) => {
  const [expanded, setExpanded] = useState(true);

  // live balance fetched from /fetchbalance (falls back to leave doc values until loaded)
  const [slBal, setSlBal] = useState<number>(leave.sl_balance);
  const [urtiBal, setUrtiBal] = useState<number>(leave.urti_balance);
  const [balanceFetching, setBalanceFetching] = useState(false);

  // Fetch live balance whenever the card is visible
  useEffect(() => {
    if (!expanded || !apiBase) return;
    setBalanceFetching(true);
    fetch(`${apiBase}/api/leaves/fetchbalance?iga_code=${encodeURIComponent(leave.iga_code)}`, {cache: "no-store"})
      .then(async (res) => {
        if (!res.ok) return;
        const data = await res.json();
        // Backend returns a tuple serialized as [sl, urti]
        if (Array.isArray(data) && data.length >= 2) {
          setSlBal(Number(data[0]) ?? leave.sl_balance);
          setUrtiBal(Number(data[1]) ?? leave.urti_balance);
        } else if (data?.sl_balance !== undefined) {
          setSlBal(Number(data.sl_balance));
          setUrtiBal(Number(data.urti_balance));
        }
      })
      .catch(() => { /* keep stale values */ })
      .finally(() => setBalanceFetching(false));
  }, [expanded, apiBase, leave.iga_code]);

  // sickness
  const [sickness, setSickness] = useState(leave.medical_issue ?? "");
  const isUrti = isUrtiRelated(sickness);

  // allocation – starts from DB value, updates live via local computation then API debounce
  const localAlloc = useMemo(
    () => computeAllocationLocal(leave.leave_duration_days, slBal, urtiBal, sickness),
    [sickness, leave.leave_duration_days, slBal, urtiBal]
  );
  const [slDays, setSlDays] = useState<number>(leave.allocation.sl_days);
  const [urtiDays, setUrtiDays] = useState<number>(leave.allocation.urti_days);
  const [lwpDays, setLwpDays] = useState<number>(leave.allocation.lwp_days);
  const [allocFetching, setAllocFetching] = useState(false);
  const [agentNote, setAgentNote] = useState<string>("");
  // debounceRef removed — allocation suggestions now computed locally only

  // document (support multiple files)
  const docIds = useMemo(() => Array.isArray(leave.document_ids) ? leave.document_ids : [], [leave.document_ids]);
  const [selectedFiles, setSelectedFiles] = useState<File[]>([]);
  const [uploadedFileIds, setUploadedFileIds] = useState<string[]>(() => docIds.slice());
  const [uploadedFilesMeta, setUploadedFilesMeta] = useState<Record<string, string>>(() => {
    const m: Record<string, string> = {};
    docIds.forEach((id) => { m[id] = ""; });
    return m;
  });
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // submit
  const [submitting, setSubmitting] = useState(false);
  const [submitted, setSubmitted] = useState(leave.crew_status === "SUBMITTED");
  const [submitError, setSubmitError] = useState<string | null>(null);

  // Whenever local allocation updates, push it to the editable fields immediately
  useEffect(() => {
    setSlDays(localAlloc.sl_days);
    setUrtiDays(localAlloc.urti_days);
    setLwpDays(localAlloc.lwp_days);
  }, [localAlloc]);

  // Allocation is now determined entirely via computeAllocationLocal.
  // Keep allocFetching false and rely on `localAlloc` -> effect to update fields.

  const handleUpload = async () => {
    if (!selectedFiles || selectedFiles.length === 0) return;
    setUploading(true);
    setUploadError(null);
    try {
      for (const f of selectedFiles) {
        const fd = new FormData();
        fd.append("report", f);
        const res = await fetch(`${apiBase}/api/leaves/doc?leave_id=${encodeURIComponent(leave.leave_id)}`, { method: "POST", body: fd });
        const json = await res.json();
        if (!res.ok) throw new Error(json.detail ?? json.message ?? "Upload failed");
        if (typeof json.message === "object" && json.message?.status === "fail") {
          throw new Error("Document validation failed. Please upload a valid medical certificate.");
        }
        const fileId = json.file_id ?? null;
        const filename = json.filename ?? f.name;
        if (fileId) {
          setUploadedFileIds((prev) => prev.includes(fileId) ? prev : [...prev, fileId]);
          setUploadedFilesMeta((prev) => ({ ...prev, [fileId]: filename }));
        }
      }
      setSelectedFiles([]);
      if (fileInputRef.current) fileInputRef.current.value = "";
    } catch (err: any) {
      setUploadError(err.message ?? "Upload failed");
    } finally {
      setUploading(false);
    }
  };

  const handleDeleteFile = async (fileId: string) => {
    if (!apiBase) { setUploadError("API base not configured"); return; }
    setUploading(true);
    setUploadError(null);
    try {
      const res = await fetch(`${apiBase}/api/leaves/doc/delete/${encodeURIComponent(leave.leave_id)}/${encodeURIComponent(fileId)}`, { method: "PATCH" });
      const json = await res.json();
      if (!res.ok) throw new Error(json.detail ?? json.message ?? "Delete failed");
      setUploadedFileIds((prev) => prev.filter((id) => id !== fileId));
      setUploadedFilesMeta((prev) => {
        const copy = { ...prev };
        delete copy[fileId];
        return copy;
      });
    } catch (err: any) {
      setUploadError(err.message ?? "Delete failed");
    } finally {
      setUploading(false);
    }
  };

  const handleSubmit = async () => {
    setSubmitting(true);
    setSubmitError(null);
    try {
      const allDocIds = Array.from(new Set([...uploadedFileIds]));

      const payload = {
        ...leave,
        sl_balance: slBal,
        urti_balance: urtiBal,
        medical_issue: sickness || null,
        allocation: { sl_days: slDays, urti_days: urtiDays, lwp_days: lwpDays },
        crew_status: "SUBMITTED",
        document_ids: allDocIds,
        updated_at: new Date().toISOString(),
      };

      const res = await fetch(`${apiBase}/api/leaves/apply`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const json = await res.json();
      if (!res.ok) throw new Error(json.detail ?? "Submission failed");
      if (json.message && json.message !== "Submitted Successfully") {
        throw new Error(json.message);
      }
      setSubmitted(true);
      onSuccess();
    } catch (err: any) {
      setSubmitError(err.message ?? "Submission failed");
    } finally {
      setSubmitting(false);
    }
  };

  const canSubmit = !submitted && !submitting && (!leave.document_required || uploadedFileIds.length > 0);

  const statusColour =
    leave.status.toUpperCase() === "PENDING"  ? "bg-amber-100 text-amber-800 border-amber-200" :
    leave.status.toUpperCase() === "APPROVED" ? "bg-green-100 text-green-800 border-green-200" :
    "bg-red-100 text-red-800 border-red-200";

  return (
    <div className="card-glass rounded-2xl border border-indigo-100 overflow-hidden">
      {/* ── Card header ── */}
      <button
        className="w-full flex items-center justify-between p-4 sm:p-5 bg-gradient-to-r from-[#000099]/5 to-indigo-50 hover:from-[#000099]/10 transition-colors"
        onClick={() => setExpanded((v) => !v)}
      >
        <div className="flex flex-wrap items-center gap-2 text-left">
          <span className="font-bold text-[#000099] text-sm">{leave.leave_id}</span>
          <span className="text-xs text-gray-500">
            {formatDate(leave.start_date)} – {formatDate(leave.end_date)}
          </span>
          <span className="text-xs text-gray-500">({leave.leave_duration_days} day{leave.leave_duration_days !== 1 ? "s" : ""})</span>
          <span className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-semibold border ${statusColour}`}>
            {leave.status}
          </span>
          {submitted && (
            <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-semibold bg-blue-100 text-blue-800 border border-blue-200">
              <CheckCircle className="h-3 w-3" /> Submitted
            </span>
          )}
        </div>
        <div className="flex-shrink-0 text-[#000099]/50">
          {expanded ? <ChevronUp className="h-5 w-5" /> : <ChevronDown className="h-5 w-5" />}
        </div>
      </button>

      {/* ── Card body ── */}
      {expanded && (
        <div className="p-4 sm:p-6 space-y-5">
          {/* Success banner */}
          {submitted && (
            <div className="card-glass flex items-center gap-2 rounded-xl border border-green-200 bg-green-50 px-4 py-3 text-green-700 text-sm font-medium">
              <CheckCircle2 className="h-4 w-4 flex-shrink-0" />
              Leave submitted successfully. Awaiting review.
            </div>
          )}

          {/* Balances – live from /fetchbalance */}
          <div className="grid grid-cols-2 gap-3">
            <div className="card-glass rounded-xl border border-indigo-100 p-3 text-center">
              <div className="flex items-center justify-center gap-1.5">
                <span className="text-xl font-bold text-[#000099]">{slBal}</span>
                {balanceFetching && <Loader2 className="h-3.5 w-3.5 animate-spin text-[#000099]/50" />}
              </div>
              <div className="text-xs text-gray-500 mt-0.5">SL Balance (days)</div>
            </div>
            <div className="card-glass rounded-xl border border-indigo-100 p-3 text-center">
              <div className="flex items-center justify-center gap-1.5">
                <span className="text-xl font-bold text-indigo-600">{urtiBal}</span>
                {balanceFetching && <Loader2 className="h-3.5 w-3.5 animate-spin text-indigo-400" />}
              </div>
              <div className="text-xs text-gray-500 mt-0.5">URTI Balance (days)</div>
            </div>
          </div>

          {/* Sickness */}
          <div>
            <label className="mb-2 block text-sm font-bold text-[#000099]">
              <Stethoscope className="mr-1.5 inline-block h-4 w-4" />
              Sickness Description
              {isUrti && (
                <span className="ml-2 inline-flex items-center gap-1 rounded-full bg-indigo-100 px-2 py-0.5 text-xs font-semibold text-indigo-700 border border-indigo-200">
                  URTI Detected ✓
                </span>
              )}
            </label>
            <textarea
              value={sickness}
              onChange={(e) => setSickness(e.target.value)}
              disabled={submitted}
              rows={3}
              placeholder="e.g. fever, cough, sore throat, URTI…"
              className="w-full rounded-xl border-2 border-[#000099]/20 bg-[#000099]/5 px-4 py-3 text-sm font-medium text-[#000099] outline-none transition focus:border-[#000099] disabled:opacity-60 disabled:cursor-not-allowed resize-none"
            />
            {isUrti && (
              <p className="mt-1 text-xs text-indigo-600 font-medium">
                URTI-related condition detected — URTI balance will be used first.
              </p>
            )}
          </div>

          {/* Recommended Leave Distribution – editable, updates on sickness change */}
          <div>
            <div className="mb-2 flex items-center gap-2">
              <label className="text-sm font-bold text-[#000099]">
                Recommended Leave Distribution
              </label>
              {allocFetching && (
                <span className="flex items-center gap-1 text-xs text-indigo-500">
                  <Loader2 className="h-3 w-3 animate-spin" /> Updating from API…
                </span>
              )}
            </div>
            <div className="card-glass rounded-2xl border border-[#000099]/15 bg-gradient-to-r from-[#000099]/5 to-indigo-50 p-4">
              <div className="grid grid-cols-3 gap-3">
                <div>
                  <label className="mb-1 block text-xs font-semibold text-[#000099] text-center">Sick Leave (SL)</label>
                  <input
                    type="number"
                    min={0}
                    step={0.5}
                    value={slDays}
                    onChange={(e) => setSlDays(parseFloat(e.target.value) || 0)}
                    disabled={submitted}
                    className="w-full rounded-xl border-2 border-[#000099]/20 bg-white px-3 py-2.5 text-center text-xl font-bold text-[#000099] outline-none transition focus:border-[#000099] disabled:opacity-60"
                  />
                  <p className="mt-1 text-center text-xs text-gray-400">bal: {slBal}</p>
                </div>
                <div>
                  <label className="mb-1 block text-xs font-semibold text-indigo-600 text-center">URTI Leave</label>
                  <input
                    type="number"
                    min={0}
                    step={0.5}
                    value={urtiDays}
                    onChange={(e) => setUrtiDays(parseFloat(e.target.value) || 0)}
                    disabled={submitted}
                    className="w-full rounded-xl border-2 border-indigo-200 bg-white px-3 py-2.5 text-center text-xl font-bold text-indigo-600 outline-none transition focus:border-indigo-500 disabled:opacity-60"
                  />
                  <p className="mt-1 text-center text-xs text-gray-400">bal: {urtiBal}</p>
                </div>
                <div>
                  <label className="mb-1 block text-xs font-semibold text-red-500 text-center">LWP</label>
                  <input
                    type="number"
                    min={0}
                    step={0.5}
                    value={lwpDays}
                    onChange={(e) => setLwpDays(parseFloat(e.target.value) || 0)}
                    disabled={submitted}
                    className="w-full rounded-xl border-2 border-red-200 bg-white px-3 py-2.5 text-center text-xl font-bold text-red-500 outline-none transition focus:border-red-400 disabled:opacity-60"
                  />
                  <p className="mt-1 text-center text-xs text-gray-400">Leave Without Pay</p>
                </div>
              </div>
              {/* Progress bar */}
              {leave.leave_duration_days > 0 && (
                <div className="mt-3 h-2.5 w-full overflow-hidden rounded-full flex">
                  {slDays > 0 && (
                    <div style={{ width: `${(slDays / leave.leave_duration_days) * 100}%` }} className="bg-[#000099] transition-all duration-500" />
                  )}
                  {urtiDays > 0 && (
                    <div style={{ width: `${(urtiDays / leave.leave_duration_days) * 100}%` }} className="bg-indigo-400 transition-all duration-500" />
                  )}
                  {lwpDays > 0 && (
                    <div style={{ width: `${(lwpDays / leave.leave_duration_days) * 100}%` }} className="bg-red-400 transition-all duration-500" />
                  )}
                </div>
              )}
              <div className="mt-2 flex justify-center gap-4 text-xs text-gray-500">
                <span className="flex items-center gap-1"><span className="inline-block h-2.5 w-2.5 rounded-sm bg-[#000099]" />SL</span>
                <span className="flex items-center gap-1"><span className="inline-block h-2.5 w-2.5 rounded-sm bg-indigo-400" />URTI</span>
                <span className="flex items-center gap-1"><span className="inline-block h-2.5 w-2.5 rounded-sm bg-red-400" />LWP</span>
              </div>
              {agentNote && (
                <p className="mt-3 rounded-lg bg-white/70 px-3 py-2 text-xs text-slate-600 border border-slate-200">
                  <span className="font-semibold text-[#000099]">Agent note: </span>{agentNote}
                </p>
              )}
            </div>
          </div>

          {/* Document Upload */}
          <div>
            <label className="mb-2 block text-sm font-bold text-[#000099]">
              <Upload className="mr-1.5 inline-block h-4 w-4" />
              Medical Document
              {leave.document_required
                ? <span className="ml-1 text-red-500 font-normal text-xs">* Required</span>
                : <span className="ml-1 text-gray-400 font-normal text-xs">(Optional)</span>
              }
            </label>

            <div className="space-y-3">
              {uploadedFileIds.length > 0 && (
                <div className="space-y-2">
                  {uploadedFileIds.map((fid) => (
                    <div key={fid} className="card-glass flex items-center gap-2 rounded-xl border border-green-200 bg-green-50 px-4 py-3 text-green-700 text-sm">
                      <CheckCircle2 className="h-4 w-4 flex-shrink-0" />
                      <div className="min-w-0 flex-1">
                        <div className="text-sm font-medium text-slate-800">{uploadedFilesMeta[fid] || fid}</div>
                        <div className="text-xs text-slate-500">{fid}</div>
                      </div>
                      {!submitted && (
                        <button
                          onClick={() => handleDeleteFile(fid)}
                          className="ml-auto text-gray-400 hover:text-gray-600"
                        >
                          <X className="h-4 w-4" />
                        </button>
                      )}
                    </div>
                  ))}
                </div>
              )}

              <div className="space-y-2">
                <label className="card-glass flex cursor-pointer items-center gap-3 rounded-xl border-2 border-dashed border-[#000099]/25 bg-slate-50 px-4 py-4 hover:bg-slate-100 transition-colors">
                  <FileText className="h-5 w-5 flex-shrink-0 text-[#000099]/60" />
                  <div className="min-w-0 flex-1">
                    <div className="text-sm font-semibold text-slate-800">
                      {selectedFiles.length > 0 ? selectedFiles.map(s => s.name).join(", ") : "Choose PDF, JPG, or PNG (you can add multiple)"}
                    </div>
                    <div className="text-xs text-slate-500">Medical certificate or doctor's note</div>
                  </div>
                  <input
                    ref={fileInputRef}
                    type="file"
                    accept=".pdf,.jpg,.jpeg,.png"
                    multiple
                    disabled={uploading || submitted}
                    className="hidden"
                    onChange={(e) => { setSelectedFiles(e.target.files ? Array.from(e.target.files) : []); setUploadError(null); }}
                  />
                  {selectedFiles.length > 0 && (
                    <button
                      type="button"
                      onClick={(e) => { e.preventDefault(); handleUpload(); }}
                      disabled={uploading || submitted}
                      className="flex-shrink-0 flex items-center gap-1.5 rounded-full bg-[#000099] px-3 py-1.5 text-xs font-semibold text-white hover:bg-[#000099]/80 disabled:opacity-60 transition-colors"
                    >
                      {uploading ? <><Loader2 className="h-3 w-3 animate-spin" /> Uploading…</> : <><Upload className="h-3 w-3" /> Upload</>}
                    </button>
                  )}
                </label>
                {uploadError && (
                  <p className="flex items-center gap-1.5 px-1 text-xs text-red-600">
                    <AlertCircle className="h-3 w-3" /> {uploadError}
                  </p>
                )}
              </div>
            </div>
          </div>

          {/* Submit error */}
          {submitError && (
            <div className="card-glass flex items-start gap-2 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-red-600 text-sm">
              <AlertCircle className="h-4 w-4 flex-shrink-0 mt-0.5" />
              {submitError}
            </div>
          )}

          {/* Submit row */}
          {!submitted && (
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between pt-1">
              <p className="text-xs text-gray-400">
                {leave.document_required && uploadedFileIds.length === 0
                  ? "Upload a medical document to enable submission."
                  : "Review details above, then submit."}
              </p>
              <button
                onClick={handleSubmit}
                disabled={!canSubmit}
                className="inline-flex items-center justify-center gap-2 rounded-xl bg-gradient-to-r from-[#000099] to-blue-700 px-6 py-3 text-sm font-bold text-white shadow hover:from-blue-700 hover:to-[#000099] disabled:opacity-50 disabled:cursor-not-allowed transition-all"
              >
                {submitting ? <><Loader2 className="h-4 w-4 animate-spin" /> Submitting…</> : "Submit Application"}
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
};

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

const CrewUrtiPage: React.FC = () => {
  const { getIgaCode, clearUser } = useUserData();
//   const igaCode = getIgaCode() || "21927";
  const igaCode = "21927"
  const apiBase = (window as any).IFS_365_API_URL?.trim() || "";

  const [leaves, setLeaves] = useState<LeaveRecord[]>(DUMMY_LEAVES);
  const [loading, setLoading] = useState(true);
  const [refreshKey, setRefreshKey] = useState(0);
  const [isDummy, setIsDummy] = useState(false);

  useEffect(() => {
    const load = async () => {
      setLoading(true);
      try {
        if (!apiBase) { setLeaves(DUMMY_LEAVES); setIsDummy(true); return; }

        const res = await fetch(`${apiBase}/api/leaves/fetchleaves?iga_code=${encodeURIComponent(igaCode)}`, {cache: "no-store"});
        if (!res.ok) { setLeaves(DUMMY_LEAVES); setIsDummy(true); return; }

        const data = await res.json();
        const transformed: LeaveRecord[] = (data.leaves ?? []).map((item: any) => ({
          leave_id:            item.leave_id,
          crew_name:           item.crew_name           || "Crew Member",
          iga_code:            item.iga_code             || igaCode,
          base:                item.base                 || "DEL",
          start_date:          item.start_date           || "",
          end_date:            item.end_date             || "",
          leave_duration_days: Number(item.leave_duration_days || 0),
          leave_category:      item.leave_category       || "SL",
          allocation:          item.allocation           || { sl_days: 0, urti_days: 0, lwp_days: 0 },
          sl_balance:          Number(item.sl_balance    || 0),
          urti_balance:        Number(item.urti_balance  || 0),
          medical_issue:       item.medical_issue        || "",
          document_required:   Boolean(item.document_required),
          document_ids:        Array.isArray(item.document_ids) ? item.document_ids : [],
          stage:               item.stage                || "CREW",
          status:              item.status               || "PENDING",
          crew_status:         item.crew_status          || "NOT SUBMITTED",
          remarks:             item.remarks              || "",
          created_at:          item.created_at           || "",
          updated_at:          item.updated_at           || "",
        }));

        if (transformed.length > 0) {
          setLeaves(transformed);
          setIsDummy(false);
        } else {
          setLeaves(DUMMY_LEAVES);
          setIsDummy(true);
        }
      } catch {
        setLeaves(DUMMY_LEAVES);
        setIsDummy(true);
      } finally {
        setLoading(false);
      }
    };
    load();
  }, [igaCode, apiBase, refreshKey]);

  // Leaves requiring crew action (stage === "CREW")
  const crewActionLeaves = useMemo(
    () => leaves.filter((l) => (l.stage ?? "").toUpperCase() === "CREW"),
    [leaves]
  );

  // All-leaves stats
  const stats = useMemo(() => ({
    total:    leaves.length,
    pending:  leaves.filter((l) => l.status.toUpperCase() === "PENDING").length,
    approved: leaves.filter((l) => l.status.toUpperCase() === "APPROVED").length,
    rejected: leaves.filter((l) => l.status.toUpperCase() === "REJECTED").length,
  }), [leaves]);

  return (
    <div>
      <div className="space-y-4 px-4 py-4 sm:px-6 lg:px-8 pb-10">

        {/* ── Header ── */}
        <div className="card-glass shadow-indigo rounded-2xl border border-indigo-100">
          <div className="flex flex-col gap-4 p-4 sm:p-6 lg:flex-row lg:items-center lg:justify-between">
            <div className="flex items-center gap-3">
              <div className="flex h-11 w-11 flex-shrink-0 items-center justify-center rounded-xl bg-indigo-gradient shadow-indigo">
                <Calendar className="h-6 w-6 text-white" />
              </div>
              <div>
                <h1 className="text-2xl font-bold text-gray-900">URTI Leaves — Crew</h1>
                <p className="text-sm text-gray-600">
                  Review pending leave applications, describe your sickness, upload documents, and submit.
                </p>
              </div>
            </div>
            <div className="flex items-center gap-3 self-start">
              {isDummy && (
                <span className="rounded-full bg-amber-100 px-3 py-1 text-xs font-semibold text-amber-700 border border-amber-200">
                  Demo Data
                </span>
              )}
              <button
                onClick={() => setRefreshKey((k) => k + 1)}
                disabled={loading}
                className="inline-flex items-center gap-2 rounded-xl border border-[#000099]/20 bg-white px-4 py-2 text-sm font-semibold text-[#000099] hover:bg-[#000099]/5 disabled:opacity-50 transition-colors"
              >
                {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
                Refresh
              </button>
            </div>
          </div>
        </div>

        {/* ── Stats ── */}
        <div className="card-glass grid grid-cols-2 gap-4 rounded-2xl border border-indigo-100 p-4 md:grid-cols-4">
          <div className="rounded-2xl border border-indigo-100 bg-white p-4 text-center shadow-sm">
            <div className="text-2xl font-bold text-[#000099]">{stats.total}</div>
            <div className="mt-1 text-xs text-gray-500">Total Leaves</div>
          </div>
          <div className="rounded-2xl border border-amber-100 bg-white p-4 text-center shadow-sm">
            <div className="text-2xl font-bold text-amber-600">{stats.pending}</div>
            <div className="mt-1 text-xs text-gray-500">Pending</div>
          </div>
          <div className="rounded-2xl border border-green-100 bg-white p-4 text-center shadow-sm">
            <div className="text-2xl font-bold text-green-600">{stats.approved}</div>
            <div className="mt-1 text-xs text-gray-500">Approved</div>
          </div>
          <div className="rounded-2xl border border-red-100 bg-white p-4 text-center shadow-sm">
            <div className="text-2xl font-bold text-red-600">{stats.rejected}</div>
            <div className="mt-1 text-xs text-gray-500">Rejected</div>
          </div>
        </div>

        {/* ── Loading ── */}
        {loading && (
          <div className="card-glass flex items-center justify-center rounded-2xl border border-dashed border-[#000099]/20 bg-white py-10 text-sm text-[#000099]">
            <Loader2 className="mr-2 h-5 w-5 animate-spin" /> Loading leave data…
          </div>
        )}

        {/* ── Crew Action Leaves ── */}
        {!loading && crewActionLeaves.length > 0 && (
          <div className="card-glass space-y-4 rounded-2xl border border-indigo-100 p-4 sm:p-5">
            <div>
              <h2 className="text-lg font-bold text-gray-900">Action Required ({crewActionLeaves.length})</h2>
              <p className="text-sm text-gray-500">
                Enter your sickness description — the recommended distribution updates automatically in real-time
                and syncs with the allocation API after a short pause.
              </p>
            </div>
            {crewActionLeaves.map((leave) => (
              <LeaveActionCard
                key={leave.leave_id}
                leave={leave}
                apiBase={apiBase}
                onSuccess={() => setRefreshKey((k) => k + 1)}
              />
            ))}
          </div>
        )}

        {/* ── Empty state ── */}
        {!loading && crewActionLeaves.length === 0 && (
          <div className="card-glass rounded-2xl border border-slate-200 bg-white px-6 py-12 text-center">
            <CheckCircle2 className="mx-auto h-10 w-10 text-green-400 mb-3" />
            <p className="font-semibold text-slate-700">No pending crew actions</p>
            <p className="text-sm text-slate-400 mt-1">
              All your leave applications have moved past the crew stage.
            </p>
          </div>
        )}

        {/* ── All Leaves Table ── */}
        {!loading && leaves.length > 0 && (
          <div className="card-glass overflow-hidden rounded-2xl border-2 border-[#000099]/20 bg-white shadow-sm">
            <div className="border-b border-[#000099]/10 px-4 py-3 sm:px-6">
              <h2 className="text-base font-bold text-slate-900">All Leaves</h2>
              <p className="text-xs text-slate-500">Complete history of your leave applications.</p>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full">
                <thead>
                  <tr className="bg-gradient-to-r from-[#000099] to-[#000066] text-white text-left text-xs font-semibold">
                    <th className="px-4 py-3 sm:px-6">Leave ID</th>
                    <th className="px-4 py-3 sm:px-6 hidden sm:table-cell">Period</th>
                    <th className="px-4 py-3 sm:px-6 hidden md:table-cell">Days</th>
                    <th className="px-4 py-3 sm:px-6 hidden lg:table-cell">SL / URTI / LWP</th>
                    <th className="px-4 py-3 sm:px-6 hidden md:table-cell">Stage</th>
                    <th className="px-4 py-3 sm:px-6">Status</th>
                    <th className="px-4 py-3 sm:px-6 hidden lg:table-cell">Crew Status</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-[#000099]/10">
                  {leaves.map((leave, idx) => {
                    const sc =
                      leave.status.toUpperCase() === "PENDING"  ? "bg-amber-100 text-amber-800 border-amber-200" :
                      leave.status.toUpperCase() === "APPROVED" ? "bg-green-100 text-green-800 border-green-200" :
                      "bg-red-100 text-red-800 border-red-200";
                    return (
                      <tr key={idx} className="hover:bg-[#000099]/5 transition-colors text-sm">
                        <td className="px-4 py-3 sm:px-6">
                          <span className="font-mono text-xs bg-[#000099]/10 text-[#000099] px-2 py-1 rounded-md border border-[#000099]/15">
                            {leave.leave_id}
                          </span>
                          <div className="sm:hidden mt-1 text-xs text-gray-500">
                            {formatDate(leave.start_date)} – {formatDate(leave.end_date)}
                          </div>
                        </td>
                        <td className="px-4 py-3 sm:px-6 text-xs text-gray-700 hidden sm:table-cell">
                          {formatDate(leave.start_date)} – {formatDate(leave.end_date)}
                        </td>
                        <td className="px-4 py-3 sm:px-6 text-xs text-gray-700 hidden md:table-cell">
                          {leave.leave_duration_days} day{leave.leave_duration_days !== 1 ? "s" : ""}
                        </td>
                        <td className="px-4 py-3 sm:px-6 text-xs text-gray-700 hidden lg:table-cell">
                          <span className="text-[#000099] font-semibold">{leave.allocation.sl_days}</span>
                          {" / "}
                          <span className="text-indigo-600 font-semibold">{leave.allocation.urti_days}</span>
                          {" / "}
                          <span className="text-red-500 font-semibold">{leave.allocation.lwp_days}</span>
                        </td>
                        <td className="px-4 py-3 sm:px-6 hidden md:table-cell">
                          <span className="rounded-full bg-slate-100 px-2 py-0.5 text-xs font-medium text-slate-700 border border-slate-200">
                            {leave.stage}
                          </span>
                        </td>
                        <td className="px-4 py-3 sm:px-6">
                          <span className={`inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-semibold ${sc}`}>
                            {leave.status}
                          </span>
                        </td>
                        <td className="px-4 py-3 sm:px-6 hidden lg:table-cell">
                          <span className={`inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-semibold ${
                            leave.crew_status === "SUBMITTED"
                              ? "bg-blue-100 text-blue-800 border-blue-200"
                              : "bg-gray-100 text-gray-600 border-gray-200"
                          }`}>
                            {leave.crew_status}
                          </span>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </div>
    </div>
  );
};

export default CrewUrtiPage;
