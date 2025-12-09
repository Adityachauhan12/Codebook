import React, { useState, useEffect } from "react";
import Layout from "../../components/Layout";
import { Calendar } from "lucide-react";
import DatePicker from "react-datepicker";
import "react-datepicker/dist/react-datepicker.css";
import { useUserData } from "../../hooks/useUserData";

interface LeaveRecord {
  id: string;
  iga_code: string;
  employee_name: string;
  base: string;
  start_date: string;
  end_date: string;
  duration_days: number;
  comment: string;
  status: "pending" | "approved" | "rejected";
  approved_by: string | null;
  created_by: string | null;
  rejected_by: string | null;
  rejection_reason: string | null;
  created_at: string;
  updated_at: string;
}

interface Attendance {
  name: string;
  date: string;
  base: string;
  leaveFrom: string;
  leaveTo: string;
  igaCode: string;
  status: "Pending" | "Approved" | "Rejected";
  comment: string;
  approvedBy: string | null;
  id: string;
  duration: number;
  createdBy?: {name: string, iga_code: string} | null;
  statusUpdatedBy?: {name: string, iga_code: string} | null;
  rejectionReason?: string | null;
}

const AdminUrtiPage: React.FC = () => {
  const { getUserInfo, clearUser } = useUserData();
  const [attendances, setAttendances] = useState<Attendance[]>([]);
  const [selectedBase, setSelectedBase] = useState("All");
  const [dateFilter, setDateFilter] = useState("All");
  const [customFromDate, setCustomFromDate] = useState<Date | null>(null);
  const [customToDate, setCustomToDate] = useState<Date | null>(null);
  const [statusFilter, setStatusFilter] = useState("Pending");
  const [searchKeyword, setSearchKeyword] = useState("");
  const [appliedFilters, setAppliedFilters] = useState({
    selectedBase: "All",
    dateFilter: "All",
    customFromDate: null as Date | null,
    customToDate: null as Date | null,
    statusFilter: "Pending",
    searchKeyword: "",
  });
  const [filteredAttendances, setFilteredAttendances] = useState<Attendance[]>(
    []
  );
  const [sortConfig, setSortConfig] = useState<{
    key: "date" | "leaveFrom" | null;
    direction: "asc" | "desc";
  }>({ key: null, direction: "asc" });
  const [isLoading, setIsLoading] = useState(false);
  const [isLoadingData, setIsLoadingData] = useState(true);
  const [actionLoading, setActionLoading] = useState<string | null>(null);
  const [modal, setModal] = useState<{
    show: boolean;
    type: "success" | "error" | "warning";
    message: string;
  }>({ show: false, type: "success", message: "" });
  const [showCommentModal, setShowCommentModal] = useState(false);
  const [selectedComment, setSelectedComment] = useState("");
  const [isShowingRejectionReason, setIsShowingRejectionReason] = useState(false);
  const [currentPage, setCurrentPage] = useState(1);
  const [totalPages, setTotalPages] = useState(1);
  const [totalRecords, setTotalRecords] = useState(0);
  const [statusCounts, setStatusCounts] = useState<{
    pending: number;
    approved: number;
    rejected: number;
  }>({
    pending: 0,
    approved: 0,
    rejected: 0,
  });



  // Format API date (YYYY-MM-DD or ISO) to DD-MM-YYYY
  const formatApiDateToDisplay = (dateString: string): string => {
    const date = new Date(dateString);
    const day = date.getDate().toString().padStart(2, "0");
    const month = (date.getMonth() + 1).toString().padStart(2, "0");
    const year = date.getFullYear();
    return `${day}-${month}-${year}`;
  };

  // Format date to dd/mm/yyyy
  const formatDateToDDMMYYYY = (dateString: string): string => {
    const [day, month, year] = dateString.split("-");
    return `${day}/${month}/${year}`;
  };

  // Convert dd-mm-yyyy string to Date object
  const parseDate = (dateString: string): Date => {
    const [day, month, year] = dateString.split("-");
    return new Date(parseInt(year), parseInt(month) - 1, parseInt(day));
  };

  // API function to approve/reject leave
  const approveRejectLeave = async (
    leaveId: string,
    action: "approved" | "rejected",
    comment: string
  ) => {
    try {
      setActionLoading(leaveId);

      // Get actual user info from UserContext
      const userInfo = getUserInfo();
      const fullName = userInfo ? `${userInfo.first_name || ''} ${userInfo.last_name || ''}`.trim() || "Unknown" : "Unknown";
      const igaCode = userInfo?.iga_code || "";
      
      const response = await fetch(
        `${window.IFS_365_API_URL}/api/ApproveRejectLeave/${leaveId}`,
        {
          method: "PATCH",
          headers: {
            "Content-Type": "application/json"
          },
          body: JSON.stringify({
            status: action === "approved" ? 1 : 0,
            comment: comment,
            approved_by: action === "approved" ? igaCode : null,
            rejected_by: action === "rejected" ? igaCode : null,
            rejection_reason: action === "rejected" ? comment : null,
            status_updated_by: {
              name: fullName,
              iga_code: igaCode
            }
          }),
        }
      );

      if (!response.ok) {
        const errorText = await response.text();
        console.error('API Error Response:', errorText);
        throw new Error(`Failed to update leave status: ${response.status} - ${errorText}`);
      }

      const result = await response.json();

      // Refresh data after status update
      await fetchLeaveData(currentPage, searchKeyword);

      return result;
    } catch (error) {
      console.error("Error updating leave status:", error);
      throw error;
    } finally {
      setActionLoading(null);
    }
  };

  // API function to fetch leave data
  const fetchLeaveData = async (page: number = 1, search: string = '') => {
    try {
      setIsLoadingData(true);
      const params = new URLSearchParams();

      if (appliedFilters.selectedBase !== "All") {
        params.append("base", appliedFilters.selectedBase);
      }
      if (appliedFilters.statusFilter !== "All") {
        params.append("status", appliedFilters.statusFilter.toLowerCase());
      }
      if (search.trim()) {
        params.append("search", search.trim());
      }
      
      // Add date filters
      if (appliedFilters.dateFilter === "Last Week") {
        const weekAgo = new Date();
        weekAgo.setDate(weekAgo.getDate() - 7);
        params.append("date_from", weekAgo.toISOString().split('T')[0]);
      } else if (appliedFilters.dateFilter === "Last Month") {
        const monthAgo = new Date();
        monthAgo.setMonth(monthAgo.getMonth() - 1);
        params.append("date_from", monthAgo.toISOString().split('T')[0]);
      } else if (appliedFilters.dateFilter === "Last 30 Days") {
        const thirtyDaysAgo = new Date();
        thirtyDaysAgo.setDate(thirtyDaysAgo.getDate() - 30);
        params.append("date_from", thirtyDaysAgo.toISOString().split('T')[0]);
      } else if (appliedFilters.dateFilter === "Custom" && appliedFilters.customFromDate) {
        params.append("date_from", appliedFilters.customFromDate.toISOString().split('T')[0]);
        if (appliedFilters.customToDate) {
          params.append("date_to", appliedFilters.customToDate.toISOString().split('T')[0]);
        }
      }
      
      params.append("page", page.toString());

      const url = `${window.IFS_365_API_URL}/api/listLeaves?${params.toString()}`;

      const response = await fetch(url,{
        cache: 'no-store'
      });

      if (!response.ok) throw new Error("Failed to fetch leave data");

      const response_data = await response.json();
      const data: LeaveRecord[] = response_data.data || [];

      // Transform API data to match component interface
      const transformedData: Attendance[] = data.map((leave: any) => {
        const igaCode = leave.iga_code || '';
        const formattedIgaCode = igaCode.startsWith('IGA') ? igaCode : `IGA${igaCode}`;

        return {
          id: leave.id,
          name: leave.employee_name,
          date: formatApiDateToDisplay(leave.created_at),
          base: leave.base,
          leaveFrom: formatApiDateToDisplay(leave.start_date),
          leaveTo: formatApiDateToDisplay(leave.end_date),
          igaCode: formattedIgaCode,
          status: (leave.status.charAt(0).toUpperCase() +
            leave.status.slice(1)) as "Pending" | "Approved" | "Rejected",
          comment: leave.comment || "",
          approvedBy: leave.approved_by || null,
          duration: leave.duration_days,
          createdBy: leave.created_by || null,
          statusUpdatedBy: leave.status_updated_by || null,
          rejectionReason: leave.rejection_reason || null,
        };
      });

      setAttendances(transformedData);
      
      // Set pagination data
      if (response_data.pagination) {
        setCurrentPage(response_data.pagination.page);
        setTotalPages(response_data.pagination.total_pages);
        setTotalRecords(response_data.pagination.total_records);
      }

      // Set status counts from API
      if (response_data.status_counts) {
        setStatusCounts({
          pending: response_data.status_counts.pending || 0,
          approved: response_data.status_counts.approved || 0,
          rejected: response_data.status_counts.rejected || 0,
        });
      }
    } catch (error) {
      console.error("Error fetching leave data:", error);
    } finally {
      setIsLoadingData(false);
    }
  };

  // Pagination handlers
  const handlePageChange = (page: number) => {
    setCurrentPage(page);
    fetchLeaveData(page, searchKeyword);
  };

  const handlePrevPage = () => {
    if (currentPage > 1) {
      handlePageChange(currentPage - 1);
    }
  };

  const handleNextPage = () => {
    if (currentPage < totalPages) {
      handlePageChange(currentPage + 1);
    }
  };

  const applyFiltersToData = () => {
    const now = new Date();
    let from: Date | null = null;
    let to: Date | null = null;

    if (appliedFilters.dateFilter === "Last Week") {
      from = new Date(now);
      from.setDate(now.getDate() - 7);
    } else if (appliedFilters.dateFilter === "Last Month") {
      from = new Date(now);
      from.setMonth(now.getMonth() - 1);
    } else if (appliedFilters.dateFilter === "Last 30 Days") {
      from = new Date(now);
      from.setDate(now.getDate() - 30);
    } else if (appliedFilters.dateFilter === "Custom") {
      from = appliedFilters.customFromDate;
      to = appliedFilters.customToDate;
    }

    const keyword = appliedFilters.searchKeyword.trim().toLowerCase();

    let filtered = attendances.filter((entry) => {
      const entryDate = parseDate(entry.date);
      const matchesFrom = !from || entryDate >= from;
      const matchesTo = !to || entryDate <= to;
      const matchesKeyword =
        !keyword ||
        entry.name.toLowerCase().includes(keyword) ||
        entry.igaCode.toLowerCase().includes(keyword);
      const matchesStatus = 
        appliedFilters.statusFilter === "All" || 
        entry.status === appliedFilters.statusFilter;

      return matchesFrom && matchesTo && matchesKeyword && matchesStatus;
    });

    if (sortConfig.key) {
      filtered.sort((a, b) => {
        const aDate = parseDate(
          sortConfig.key === "date" ? a.date : a.leaveFrom
        );
        const bDate = parseDate(
          sortConfig.key === "date" ? b.date : b.leaveFrom
        );
        return sortConfig.direction === "asc"
          ? aDate.getTime() - bDate.getTime()
          : bDate.getTime() - aDate.getTime();
      });
    }

    setFilteredAttendances(filtered);
  };

  const handleSort = (key: "date" | "leaveFrom") => {
    setSortConfig((prev) => ({
      key,
      direction: prev.key === key && prev.direction === "asc" ? "desc" : "asc",
    }));
  };

  const LoadingSpinner = () => (
    <div className="flex flex-col items-center justify-center h-64 space-y-4">
      <div className="relative">
        <div className="w-16 h-16 border-4 border-indigo-primary/30 border-t-indigo-primary rounded-full animate-spin"></div>
        <div className="absolute inset-0 flex items-center justify-center">
          <div className="w-8 h-8 bg-indigo-gradient rounded-full animate-pulse"></div>
        </div>
      </div>
      <div className="text-center">
        <p className="text-indigo-primary font-semibold">
          Loading Leaves data...
        </p>
        <p className="text-sm text-muted-foreground">Please wait a moment.</p>
      </div>
    </div>
  );

  const handleApplyFilters = () => {
    setIsLoading(true);
    setAppliedFilters({
      selectedBase,
      dateFilter,
      customFromDate,
      customToDate,
      statusFilter,
      searchKeyword,
    });
    setTimeout(() => setIsLoading(false), 500);
  };

  const handleClearFilters = () => {
    setSelectedBase("All");
    setDateFilter("All");
    setCustomFromDate(null);
    setCustomToDate(null);
    setStatusFilter("Pending");
    setSearchKeyword("");
    setAppliedFilters({
      selectedBase: "All",
      dateFilter: "All",
      customFromDate: null,
      customToDate: null,
      statusFilter: "Pending",
      searchKeyword: "",
    });
  };

  const showModal = (
    type: "success" | "error" | "warning",
    message: string
  ) => {
    setModal({ show: true, type, message });
  };

  const handleViewComment = (comment: string, isRejectionReason = false) => {
    setSelectedComment(comment);
    setIsShowingRejectionReason(isRejectionReason);
    setShowCommentModal(true);
  };

  const handleStatusUpdate = async (
    index: number,
    newStatus: Attendance["status"]
  ) => {
    const leave = filteredAttendances[index];
    if (!leave.comment.trim()) {
      showModal("warning", "Please add a comment before Approving/Rejecting");
      return;
    }
    try {
      await approveRejectLeave(
        leave.id,
        newStatus.toLowerCase() as "approved" | "rejected",
        leave.comment
      );
      showModal(
        "success",
        `Leave request has been ${newStatus.toLowerCase()} successfully!\nPlease go to "${newStatus}" filter to see updates.`
      );
    } catch (error) {
      console.error("Failed to update status:", error);
      showModal("error", "Failed to update leave status. Please try again.");
    }
  };

  const updateComment = (index: number, newComment: string) => {
    const updated = [...filteredAttendances];
    updated[index].comment = newComment;
    setAttendances((prev) =>
      prev.map((entry) =>
        entry.id === updated[index].id
          ? { ...entry, comment: newComment }
          : entry
      )
    );
    setFilteredAttendances(updated);
  };

  // Check if comment should be locked (status is not Pending)
  const isCommentLocked = (status: Attendance["status"]) => {
    return status !== "Pending";
  };

  const getBadgeClasses = (status: Attendance["status"]) => {
    const base =
      "inline-flex items-center px-3 py-1 rounded-full text-sm font-semibold border transition-all duration-200";

    if (status === "Approved")
      return `${base} bg-green-100 text-green-800 border-green-400`;
    if (status === "Rejected")
      return `${base} bg-red-100 text-red-800 border-red-200`;
    return `${base} bg-yellow-100 text-yellow-800 border-yellow-200`;
  };

  useEffect(() => {
    setCurrentPage(1); // Reset to first page when filters change
    fetchLeaveData(1, searchKeyword);
  }, [appliedFilters.selectedBase, appliedFilters.statusFilter, appliedFilters.dateFilter, appliedFilters.customFromDate, appliedFilters.customToDate]);

  // Trigger search when searchKeyword changes
  useEffect(() => {
    const timeoutId = setTimeout(() => {
      setCurrentPage(1);
      fetchLeaveData(1, searchKeyword);
    }, 300); // Debounce search by 300ms

    return () => clearTimeout(timeoutId);
  }, [searchKeyword]);

  useEffect(
    () => applyFiltersToData(),
    [attendances, appliedFilters, sortConfig]
  );
  useEffect(
    () =>
      setAppliedFilters((prev) => ({
        ...prev,
        searchKeyword,
      })),
    [searchKeyword]
  );

  // Prevent body scroll when modal is open
  useEffect(() => {
    if (showCommentModal) {
      document.body.style.overflow = 'hidden';
    } else {
      document.body.style.overflow = 'unset';
    }
    
    // Cleanup on unmount
    return () => {
      document.body.style.overflow = 'unset';
    };
  }, [showCommentModal]);

  return (
    <Layout userRole="Admin">
      {/* Modal Popup */}
      {modal.show && (
        <div className="fixed inset-0 z-50 flex items-center justify-center">
          <div className="fixed inset-0 bg-black/30" />
          <div className="relative bg-white rounded-lg shadow-lg p-8 max-w-lg w-full mx-4 border-2 border-indigo-primary min-h-[200px] flex flex-col justify-between">
            <button
              onClick={() => setModal({ ...modal, show: false })}
              className="absolute top-3 right-3 text-gray-400 hover:text-gray-600"
            >
              <svg
                className="w-5 h-5"
                fill="none"
                stroke="currentColor"
                viewBox="0 0 24 24"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={2}
                  d="M6 18L18 6M6 6l12 12"
                />
              </svg>
            </button>
            <p className="text-gray-800 text-center text-lg mb-8 mt-4 whitespace-pre-wrap">
              {modal.message}
            </p>
            <div className="flex justify-center">
              <button
                onClick={() => setModal({ ...modal, show: false })}
                className="px-6 py-2 bg-indigo-primary text-white rounded-lg hover:bg-indigo-primary/90 transition-colors"
              >
                OK
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Comment Modal */}
      {showCommentModal && (
        <div className="fixed inset-0 z-50" style={{overflow: 'hidden'}}>
          <div 
            className="absolute inset-0 bg-black bg-opacity-50"
            onClick={() => setShowCommentModal(false)}
          />
          <div className="relative h-full flex items-center justify-center p-4">
            <div 
              className="bg-white rounded-2xl shadow-2xl w-full max-w-md" 
              style={{maxHeight: '90vh', display: 'flex', flexDirection: 'column'}}
              onClick={(e) => e.stopPropagation()}
            >
              <div className="flex justify-between items-center p-6 border-b" style={{flexShrink: 0}}>
                <h2 className="text-xl font-bold text-gray-900">
                  {isShowingRejectionReason ? "Rejection Reason" : "Comment"}
                </h2>
                <button
                  onClick={() => setShowCommentModal(false)}
                  className="text-gray-400 hover:text-gray-600 text-2xl font-bold"
                >
                  ×
                </button>
              </div>
              <div className="p-6" style={{flex: 1, minHeight: 0, overflow: 'auto'}}>
                <p className="text-gray-700 whitespace-pre-wrap break-words">
                  {selectedComment}
                </p>
              </div>
              <div className="flex justify-end p-6 border-t" style={{flexShrink: 0}}>
                <button
                  onClick={() => setShowCommentModal(false)}
                  className="px-4 py-2 bg-gray-500 text-white rounded-lg hover:bg-gray-600"
                >
                  Close
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      <div className="space-y-4">
        {/* Header Card */}
        <div className="card-glass shadow-indigo border border-indigo-100">
          <div className="p-1">
            <div className="flex flex-col lg:flex-row lg:items-center justify-between gap-4">
              <div className="flex items-center gap-3">
                <div className="w-10 h-10 bg-indigo-gradient rounded-xl flex items-center justify-center shadow-indigo">
                  <Calendar className="h-6 w-6 text-white"></Calendar>
                </div>

                <div>
                  <h1 className="text-3xl font-bold text-gray-900 mb-1">
                    URTI Leave Management
                  </h1>
                  <p className=" text-gray-600">
                    Manage and review URTI leave requests
                  </p>
                </div>
              </div>
            </div>
          </div>
        </div>

        {/* Filters + Stats using your glass utility */}
        <div className="flex flex-wrap items-end gap-3 lg:gap-4 glass rounded-2xl shadow-indigo animate-fade-in-up animate-stagger-2 p-3">
          {/* filters */}
          <div className="flex flex-wrap items-end gap-3">
            <div className="w-36">
              <label className="block text-xs font-medium text-indigo-primary mb-1">
                Base
              </label>
              <select
                value={selectedBase}
                onChange={(e) => setSelectedBase(e.target.value)}
                className="w-full px-3 py-2 border-2 border-indigo-primary/30 rounded-lg text-sm focus:ring-indigo-primary/50 focus:border-indigo-primary"
              >
                <option value="All">All Bases</option>
                {((window as any).BASES || []).map((base: string) => (
                  <option key={base} value={base}>{base}</option>
                ))}
              </select>
            </div>
            <div className="w-36">
              <label className="block text-xs font-medium text-indigo-primary mb-1">
                Date Applied
              </label>
              <select
                value={dateFilter}
                onChange={(e) => setDateFilter(e.target.value)}
                className="w-full px-3 py-2 border-2 border-indigo-primary/30 rounded-lg text-sm focus:ring-indigo-primary/50 focus:border-indigo-primary"
              >
                <option value="All">All Dates</option>
                <option value="Last Week">Last Week</option>
                <option value="Last Month">Last Month</option>
                <option value="Last 30 Days">Last 30 Days</option>
                <option value="Custom">Custom</option>
              </select>
            </div>
            <div className="w-32">
              <label className="block text-xs font-medium text-indigo-primary mb-1">
                Status
              </label>
              <select
                value={statusFilter}
                onChange={(e) => setStatusFilter(e.target.value)}
                className="w-full px-3 py-2 border-2 border-indigo-primary/30 rounded-lg text-sm focus:ring-indigo-primary/50 focus:border-indigo-primary"
              >
                <option value="All">All Status</option>
                <option value="Pending">Pending</option>
                <option value="Approved">Approved</option>
                <option value="Rejected">Rejected</option>
              </select>
            </div>
            <button
              onClick={handleApplyFilters}
              disabled={isLoading}
              className="btn-indigo disabled:opacity-50"
            >
              {isLoading ? "Applying…" : "Apply Filters"}
            </button>
            <button
              onClick={handleClearFilters}
              className="h-10 px-4 text-sm font-medium text-gray-700 bg-white border border-gray-300 rounded-lg hover:bg-gray-50 hover:border-gray-400 transition-colors"
            >
              Clear All
            </button>
          </div>

          {/* Stats */}
          <div className="flex gap-3 ml-auto">
            {appliedFilters.statusFilter === "All" && (
              <div className="min-w-[80px] rounded-xl border-2 border-indigo-primary/20 bg-white/90 py-1.5 px-3 text-center">
                <div className="text-xs font-bold text-indigo-primary">Total</div>
                <div className="text-lg font-extrabold text-indigo-primary">
                  {statusCounts.pending + statusCounts.approved + statusCounts.rejected}
                </div>
              </div>
            )}
            {(appliedFilters.statusFilter === "All" || appliedFilters.statusFilter === "Pending") && (
              <div className="min-w-[80px] rounded-xl border-2 border-indigo-primary/20 bg-white/90 py-1.5 px-3 text-center">
                <div className="text-xs font-bold text-amber-600">Pending</div>
                <div className="text-lg font-extrabold text-amber-600">
                  {statusCounts.pending}
                </div>
              </div>
            )}
            {(appliedFilters.statusFilter === "All" || appliedFilters.statusFilter === "Approved") && (
              <div className="min-w-[80px] rounded-xl border-2 border-indigo-primary/20 bg-white/90 py-1.5 px-3 text-center">
                <div className="text-xs font-bold text-[rgb(139,170,21)]">
                  Approved
                </div>
                <div className="text-lg font-extrabold text-[rgb(139,170,21)]">
                  {statusCounts.approved}
                </div>
              </div>
            )}
            {(appliedFilters.statusFilter === "All" || appliedFilters.statusFilter === "Rejected") && (
              <div className="min-w-[80px] rounded-xl border-2 border-indigo-primary/20 bg-white/90 py-1.5 px-3 text-center">
                <div className="text-xs font-bold text-[rgb(224,107,20)]">
                  Rejected
                </div>
                <div className="text-lg font-extrabold text-[rgb(224,107,20)]">
                  {statusCounts.rejected}
                </div>
              </div>
            )}
          </div>
        </div>

        {/* Custom date range */}
        {dateFilter === "Custom" && (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4 card-glass animate-slide-down relative z-50">
            <div className="relative z-50">
              <label className="block text-xs font-medium text-indigo-primary mb-1">
                From{" "}
                <span className="text-xs font-normal text-gray-500">
                  (DD/MM/YYYY)
                </span>
              </label>
              <DatePicker
                selected={customFromDate}
                onChange={(date: Date | null) => setCustomFromDate(date)}
                dateFormat="dd/MM/yyyy"
                placeholderText="Select date"
                maxDate={new Date()}
                className="w-full px-3 py-2 border-2 border-indigo-primary/30 rounded-lg text-sm focus:ring-indigo-primary/50 focus:border-indigo-primary"
                wrapperClassName="w-full"
                popperClassName="z-50"
              />
            </div>
            <div className="relative z-50">
              <label className="block text-xs font-medium text-indigo-primary mb-1">
                To{" "}
                <span className="text-xs font-normal text-gray-500">
                  (DD/MM/YYYY)
                </span>
              </label>
              <DatePicker
                selected={customToDate}
                onChange={(date: Date | null) => setCustomToDate(date)}
                dateFormat="dd/MM/yyyy"
                placeholderText="Select date"
                minDate={customFromDate || undefined}
                maxDate={new Date()}
                className="w-full px-3 py-2 border-2 border-indigo-primary/30 rounded-lg text-sm focus:ring-indigo-primary/50 focus:border-indigo-primary"
                wrapperClassName="w-full"
                popperClassName="z-50"
              />
            </div>
          </div>
        )}

        {/* Search bar */}
        <div className="w-full">
          <div
            className={`p-[3px] rounded-full bg-gradient-to-r from-[#000099] to-indigo-600 transition-all duration-300 ${
              searchKeyword.trim() !== ""
                ? "shadow-[0_0_12px_4px_rgba(0,0,153,0.4)]"
                : "shadow-[0_0_8px_2px_rgba(0,0,153,0.2)]"
            }`}
          >
            <div className="relative bg-white rounded-full">
              <div className="absolute left-4 top-1/2 transform -translate-y-1/2">
                <svg
                  className="w-5 h-5 text-gray-400"
                  fill="none"
                  stroke="currentColor"
                  viewBox="0 0 24 24"
                >
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    strokeWidth={2}
                    d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"
                  />
                </svg>
              </div>
              <input
                value={searchKeyword}
                onChange={(e) => setSearchKeyword(e.target.value)}
                placeholder="Search by IGA code or name"
                className="w-full pl-12 pr-12 py-3 text-sm bg-transparent rounded-full focus:outline-none"
              />
              {searchKeyword && (
                <button
                  onClick={() => setSearchKeyword("")}
                  className="absolute right-3 top-1/2 transform -translate-y-1/2 p-1 text-gray-400 hover:text-red-500 transition-colors duration-200"
                >
                  <svg
                    className="h-4 w-4"
                    fill="none"
                    stroke="currentColor"
                    viewBox="0 0 24 24"
                  >
                    <path
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      strokeWidth={2}
                      d="M6 18L18 6M6 6l12 12"
                    />
                  </svg>
                </button>
              )}
            </div>
          </div>
        </div>

        {/* Table using your glass utility */}
        <div className="glass rounded-2xl shadow-indigo-lg overflow-hidden animate-fade-in-up animate-stagger-3">
          {isLoadingData ? (
            <LoadingSpinner />
          ) : (
            <div className="overflow-x-auto scrollbar-thin">
              <table className="w-full">
                <thead>
                  <tr className="bg-indigo-gradient text-white">
                    <th className="px-6 py-4 text-left font-semibold">
                      IGA Code
                    </th>
                    <th className="px-4 py-4 text-left font-semibold w-32">
                      Name
                    </th>
                    <th
                      className="px-4 py-4 text-left font-semibold cursor-pointer hover:bg-white/10 transition-colors select-none w-28"
                      onClick={() => handleSort("date")}
                    >
                      <div className="flex items-center gap-1">
                        Date Applied
                        <span className="text-xs">
                          {sortConfig.key === "date"
                            ? sortConfig.direction === "asc"
                              ? "▲"
                              : "▼"
                            : ""}
                        </span>
                      </div>
                    </th>
                    <th className="px-6 py-4 text-left font-semibold">Base</th>
                    <th
                      className="px-6 py-4 text-left font-semibold cursor-pointer hover:bg-white/10 transition-colors select-none"
                      onClick={() => handleSort("leaveFrom")}
                    >
                      <div className="flex items-center gap-1">
                        Leave Period
                        <span className="text-xs">
                          {sortConfig.key === "leaveFrom"
                            ? sortConfig.direction === "asc"
                              ? "▲"
                              : "▼"
                            : ""}
                        </span>
                      </div>
                    </th>
                    <th className="px-6 py-4 text-left font-semibold">
                      Applied By
                    </th>
                    <th className="px-6 py-4 text-left font-semibold">
                      Status
                    </th>
                    <th className="px-6 py-4 text-left font-semibold min-w-[200px]">
                      Comment
                    </th>
                    <th className="px-6 py-4 text-left font-semibold">
                      <div className="whitespace-nowrap">
                        Actions / Reviewed By
                      </div>
                    </th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-indigo-primary/10">
                  {filteredAttendances.map((e, idx) => (
                    <tr
                      key={e.id}
                      className={`hover:bg-indigo-primary/5 transition-colors animate-fadeInUp delay-[${
                        idx * 50
                      }ms]`}
                    >
                      <td className="px-6 py-3">
                        <span className="font-mono text-sm bg-indigo-primary/10 text-indigo-primary px-2 py-1 rounded-md border border-indigo-primary/20">
                          {e.igaCode}
                        </span>
                      </td>
                      <td className="px-4 py-3 w-24">{e.name}</td>
                      <td className="px-4 py-3 font-medium text-sm w-22">
                        {formatDateToDDMMYYYY(e.date)}
                      </td>
                      <td className="px-6 py-3">
                        <span className="px-3 py-1 rounded-full text-xs font-medium bg-indigo-primary/10 text-indigo-primary border border-indigo-primary/20">
                          {e.base}
                        </span>
                      </td>
                      <td className="px-6 py-3 font-medium text-sm">
                        {formatDateToDDMMYYYY(e.leaveFrom)} to{" "}
                        {formatDateToDDMMYYYY(e.leaveTo)} ({e.duration} {e.duration === 1 ? 'day' : 'days'})
                      </td>
                      <td className="px-6 py-3 text-sm">
                        {e.createdBy ? 
                          `${e.createdBy.name} (${e.createdBy.iga_code})` : 
                          '--'
                        }
                      </td>
                      <td className="px-6 py-3">
                        <span className={getBadgeClasses(e.status)}>
                          {e.status}
                        </span>
                      </td>
                      <td className="px-6 py-3">
                        {isCommentLocked(e.status) ? (
                          e.status === "Rejected" && e.rejectionReason ? (
                            <div className="space-y-1">
                              <button
                                onClick={() => handleViewComment(e.rejectionReason!, true)}
                                className="text-red-600 hover:text-red-800 underline text-sm font-medium"
                              >
                                View Rejection Reason
                              </button>
                            </div>
                          ) : e.comment ? (
                            <button
                              onClick={() => handleViewComment(e.comment)}
                              className="text-indigo-primary hover:text-blue-800 underline text-sm"
                            >
                              View Comment
                            </button>
                          ) : (
                            <span className="text-gray-400 text-sm">
                              No comment
                            </span>
                          )
                        ) : (
                          <div className="min-w-[180px]">
                            <input
                              value={e.comment}
                              onChange={(ev) =>
                                updateComment(idx, ev.target.value)
                              }
                              maxLength={100}
                              className="w-full min-w-[180px] px-3 py-2 border-2 rounded-lg text-sm transition-all border-indigo-primary/20 focus:ring-indigo-primary/50 focus:border-indigo-primary bg-white"
                              placeholder="Add comment…"
                            />
                            {e.comment.length > 100 && (
                              <p className="text-xs text-red-500 mt-1">
                                Character limit exceeded
                              </p>
                            )}
                          </div>
                        )}
                      </td>
                      <td className="px-6 py-3">
                        <div className="flex flex-col items-center gap-2">
                          <div className="flex gap-2 justify-center">
                            {e.status === "Pending" && (
                              <button
                                onClick={() =>
                                  handleStatusUpdate(idx, "Approved")
                                }
                                disabled={actionLoading === e.id}
                                className="px-4 py-1.5 text-xs font-medium text-white rounded-lg disabled:opacity-50 hover:brightness-110 transition-all animate-scale-hover bg-gradient-to-r from-[rgb(139,170,21)] to-[rgb(111,134,18)]"
                              >
                                {actionLoading === e.id ? "..." : "Approve"}
                              </button>
                            )}
                            {e.status === "Pending" && (
                              <button
                                onClick={() =>
                                  handleStatusUpdate(idx, "Rejected")
                                }
                                disabled={actionLoading === e.id}
                                className="px-4 py-1.5 text-xs font-medium text-white rounded-lg disabled:opacity-50 hover:brightness-110 transition-all animate-scale-hover bg-gradient-to-r from-[rgb(224,107,20)] to-[rgb(181,84,15)]"
                              >
                                {actionLoading === e.id ? "..." : "Reject"}
                              </button>
                            )}
                            {(e.status === "Approved" ||
                              e.status === "Rejected") && (
                              <span className="px-4 py-1.5 text-xs font-medium text-gray-500 bg-gray-100 rounded-lg">
                                {e.status === "Approved"
                                  ? "Approved"
                                  : "Rejected"}
                              </span>
                            )}
                          </div>
                          {e.statusUpdatedBy && (
                            <div className="text-xs text-gray-600 text-center">
                              {e.statusUpdatedBy.name} ({e.statusUpdatedBy.iga_code})
                            </div>
                          )}
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>

              {!filteredAttendances.length && !isLoadingData && (
                <div className="py-10 text-center text-indigo-primary/70 animate-fade-in-up">
                  No records found
                </div>
              )}
            </div>
          )}
          
          {/* Pagination */}
          {!isLoadingData && totalPages > 1 && (
            <div className="px-6 py-4 bg-gray-50 border-t border-gray-200 flex items-center justify-between">
              <div className="text-sm text-gray-700">
                Showing page {currentPage} of {totalPages} ({totalRecords} total records)
              </div>
              <div className="flex items-center gap-2">
                <button
                  onClick={() => handlePageChange(1)}
                  disabled={currentPage === 1}
                  className="px-3 py-2 text-sm font-medium text-gray-500 bg-white border border-gray-300 rounded-md hover:bg-gray-50 disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  First
                </button>
                <button
                  onClick={handlePrevPage}
                  disabled={currentPage === 1}
                  className="px-3 py-2 text-sm font-medium text-gray-500 bg-white border border-gray-300 rounded-md hover:bg-gray-50 disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  Previous
                </button>
                
                <div className="flex items-center gap-1">
                  {Array.from({ length: Math.min(5, totalPages) }, (_, i) => {
                    let pageNum;
                    if (totalPages <= 5) {
                      pageNum = i + 1;
                    } else if (currentPage <= 3) {
                      pageNum = i + 1;
                    } else if (currentPage >= totalPages - 2) {
                      pageNum = totalPages - 4 + i;
                    } else {
                      pageNum = currentPage - 2 + i;
                    }
                    
                    return (
                      <button
                        key={pageNum}
                        onClick={() => handlePageChange(pageNum)}
                        className={`px-3 py-2 text-sm font-medium rounded-md ${
                          currentPage === pageNum
                            ? 'bg-indigo-600 text-white'
                            : 'text-gray-500 bg-white border border-gray-300 hover:bg-gray-50'
                        }`}
                      >
                        {pageNum}
                      </button>
                    );
                  })}
                </div>
                
                <button
                  onClick={handleNextPage}
                  disabled={currentPage === totalPages}
                  className="px-3 py-2 text-sm font-medium text-gray-500 bg-white border border-gray-300 rounded-md hover:bg-gray-50 disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  Next
                </button>
                <button
                  onClick={() => handlePageChange(totalPages)}
                  disabled={currentPage === totalPages}
                  className="px-3 py-2 text-sm font-medium text-gray-500 bg-white border border-gray-300 rounded-md hover:bg-gray-50 disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  Last
                </button>
              </div>
            </div>
          )}
        </div>
      </div>
    </Layout>
  );
};

export default AdminUrtiPage;
