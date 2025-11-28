import React, { useState, useEffect } from "react";
import Layout from "../../components/Layout";
import { BarChart3, TrendingUp, Brain, Activity, Search, Calendar, Users, PieChart, LineChart } from "lucide-react";
import {
  LineChart as RechartsLineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ResponsiveContainer,
  BarChart as RechartsBarChart,
  Bar,
  PieChart as RechartsPieChart,
  Cell,
  Pie,
} from "recharts";

// Interface definitions
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
}

type ChartType = "duration" | "seasonal" | "base";

const UrtiAIAnalyticsAdminPage: React.FC = () => {
  const [isLoading, setIsLoading] = useState(true);
  const [attendances, setAttendances] = useState<Attendance[]>([]);
  const [filteredData, setFilteredData] = useState<Attendance[]>([]);
  const [searchKeyword, setSearchKeyword] = useState("");

  const handleLogout = () => {
    localStorage.clear();
    window.location.href = "/";
  };

  // Format API date (YYYY-MM-DD or ISO) to DD-MM-YYYY
  const formatApiDateToDisplay = (dateString: string): string => {
    const date = new Date(dateString);
    const day = date.getDate().toString().padStart(2, "0");
    const month = (date.getMonth() + 1).toString().padStart(2, "0");
    const year = date.getFullYear();
    return `${day}-${month}-${year}`;
  };

  // API function to fetch leave data (copied from admin page)
  const fetchLeaveData = async () => {
    try {
      setIsLoading(true);
      const url = `${window.IFS_365_API_URL}/api/list_leaves_analytics`;
      const response = await fetch(url);

      if (!response.ok) throw new Error("Failed to fetch leave data");

      const data: LeaveRecord[] = await response.json();

      // Transform API data to match component interface
      const transformedData: Attendance[] = data.map((record) => {
        const igaCode = record.iga_code || '';
        const formattedIgaCode = igaCode.startsWith('IGA') ? igaCode : `IGA${igaCode}`;
        
        return {
          id: record.id,
          name: record.employee_name,
          date: formatApiDateToDisplay(record.created_at),
          base: record.base,
          leaveFrom: formatApiDateToDisplay(record.start_date),
          leaveTo: formatApiDateToDisplay(record.end_date),
          igaCode: formattedIgaCode,
          status: (record.status.charAt(0).toUpperCase() +
            record.status.slice(1)) as "Pending" | "Approved" | "Rejected",
          comment: record.comment || "",
          approvedBy: record.approved_by,
          duration: record.duration_days,
        };
      });

      setAttendances(transformedData);
      setFilteredData(transformedData);
    } catch (error) {
      console.error("Error fetching leave data:", error);
    } finally {
      setIsLoading(false);
    }
  };

  // Search functionality
  useEffect(() => {
    if (searchKeyword.trim() === "") {
      setFilteredData(attendances);
    } else {
      const filtered = attendances.filter((entry) =>
        entry.igaCode.toLowerCase().includes(searchKeyword.toLowerCase()) ||
        entry.name.toLowerCase().includes(searchKeyword.toLowerCase())
      );
      setFilteredData(filtered);
    }
  }, [searchKeyword, attendances]);

  useEffect(() => {
    fetchLeaveData();
  }, []);

  // Data processing functions
  const getDurationPatternsData = () => {
    const dataToProcess = searchKeyword ? filteredData : attendances;
    const durationGroups = dataToProcess.reduce((acc, entry) => {
      const range = entry.duration <= 2 ? "1-2 days" : 
                   entry.duration <= 5 ? "3-5 days" :
                   entry.duration <= 10 ? "6-10 days" : "10+ days";
      acc[range] = (acc[range] || 0) + 1;
      return acc;
    }, {} as Record<string, number>);

    // Define the order of ranges
    const orderedRanges = ["1-2 days", "3-5 days", "6-10 days", "10+ days"];
    
    return orderedRanges.map(range => ({
      range,
      count: durationGroups[range] || 0,
      percentage: (((durationGroups[range] || 0) / dataToProcess.length) * 100).toFixed(1)
    }));
  };

  const getSeasonalTrendsData = () => {
    const dataToProcess = searchKeyword ? filteredData : attendances;
    const monthlyData = dataToProcess.reduce((acc, entry) => {
      const month = new Date(entry.leaveFrom.split('-').reverse().join('-')).toLocaleDateString('en-US', { month: 'short' });
      acc[month] = (acc[month] || 0) + 1;
      return acc;
    }, {} as Record<string, number>);

    const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
    return months.map(month => ({
      month,
      leaves: monthlyData[month] || 0
    }));
  };

  // New analytics functions
  const getStatusBreakdownData = () => {
    const dataToProcess = searchKeyword ? filteredData : attendances;
    const statusData = dataToProcess.reduce((acc, entry) => {
      acc[entry.status] = (acc[entry.status] || 0) + 1;
      return acc;
    }, {} as Record<string, number>);

    const colors = ['#10b981', '#f59e0b', '#ef4444']; // Green, Yellow, Red
    const statusOrder = ['Approved', 'Pending', 'Rejected'];
    
    return statusOrder.map((status, index) => ({
      name: status,
      value: statusData[status] || 0,
      color: colors[index]
    }));
  };

  const getLeaveApplicationLeadTimeData = () => {
    const dataToProcess = searchKeyword ? filteredData : attendances;
    const leadTimeGroups = dataToProcess.reduce((acc, entry) => {
      const appliedDate = new Date(entry.date.split('-').reverse().join('-'));
      const leaveFromDate = new Date(entry.leaveFrom.split('-').reverse().join('-'));
      const leadTimeDays = Math.floor((leaveFromDate.getTime() - appliedDate.getTime()) / (1000 * 60 * 60 * 24));
      
      const range = leadTimeDays < 0 ? "Same day" :
                   leadTimeDays <= 1 ? "1 day" :
                   leadTimeDays <= 3 ? "2-3 days" :
                   leadTimeDays <= 7 ? "4-7 days" :
                   leadTimeDays <= 14 ? "8-14 days" : "15+ days";
      
      acc[range] = (acc[range] || 0) + 1;
      return acc;
    }, {} as Record<string, number>);

    const orderedRanges = ["Same day", "1 day", "2-3 days", "4-7 days", "8-14 days", "15+ days"];
    
    return orderedRanges.map(range => ({
      range,
      count: leadTimeGroups[range] || 0,
      percentage: (((leadTimeGroups[range] || 0) / dataToProcess.length) * 100).toFixed(1)
    }));
  };

  const getDayOfWeekLeaveData = () => {
    const dataToProcess = searchKeyword ? filteredData : attendances;
    const dayData = dataToProcess.reduce((acc, entry) => {
      const leaveDate = new Date(entry.leaveFrom.split('-').reverse().join('-'));
      const dayName = leaveDate.toLocaleDateString('en-US', { weekday: 'short' });
      acc[dayName] = (acc[dayName] || 0) + 1;
      return acc;
    }, {} as Record<string, number>);

    const daysOrder = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
    
    return daysOrder.map(day => ({
      day,
      leaves: dayData[day] || 0
    }));
  };

  const getBaseAverageDurationData = () => {
    const dataToProcess = searchKeyword ? filteredData : attendances;
    const baseData = dataToProcess.reduce((acc, entry) => {
      if (!acc[entry.base]) {
        acc[entry.base] = { totalDuration: 0, count: 0 };
      }
      acc[entry.base].totalDuration += entry.duration;
      acc[entry.base].count += 1;
      return acc;
    }, {} as Record<string, { totalDuration: number, count: number }>);

    return Object.entries(baseData).map(([base, data]) => ({
      base,
      avgDuration: parseFloat((data.totalDuration / data.count).toFixed(1)),
      totalLeaves: data.count
    }));
  };

  const getBaseFrequencyData = () => {
    const dataToProcess = searchKeyword ? filteredData : attendances;
    const baseData = dataToProcess.reduce((acc, entry) => {
      acc[entry.base] = (acc[entry.base] || 0) + 1;
      return acc;
    }, {} as Record<string, number>);

    const colors = ['#8884d8', '#82ca9d', '#ffc658', '#ff7c7c', '#8dd1e1', '#d084d0'];
    return Object.entries(baseData).map(([base, count], index) => ({
      name: base,
      value: count,
      color: colors[index % colors.length]
    }));
  };



  // Chart rendering functions with consistent height
  const renderDurationChart = () => (
    <ResponsiveContainer width="100%" height={350}>
      <RechartsBarChart data={getDurationPatternsData()}>
        <CartesianGrid strokeDasharray="3 3" />
        <XAxis dataKey="range" />
        <YAxis />
        <Tooltip formatter={(value, name) => [value, "Leave Count"]} />
        <Legend />
        <Bar dataKey="count" fill="#6366f1" radius={[4, 4, 0, 0]} />
      </RechartsBarChart>
    </ResponsiveContainer>
  );

  const renderSeasonalChart = () => (
    <ResponsiveContainer width="100%" height={350}>
      <RechartsLineChart data={getSeasonalTrendsData()}>
        <CartesianGrid strokeDasharray="3 3" />
        <XAxis dataKey="month" />
        <YAxis />
        <Tooltip formatter={(value, name) => [value, "Leave Count"]} />
        <Legend />
        <Line 
          type="monotone" 
          dataKey="leaves" 
          stroke="#10b981" 
          strokeWidth={3}
          dot={{ fill: '#10b981', strokeWidth: 2, r: 6 }}
        />
      </RechartsLineChart>
    </ResponsiveContainer>
  );

  const renderBaseChart = () => {
    const data = getBaseFrequencyData();
    const midpoint = Math.ceil(data.length / 2);
    const leftSide = data.slice(0, midpoint);
    const rightSide = data.slice(midpoint);
    
    return (
      <div className="flex items-center justify-center gap-6">
        {/* Left Legend */}
        <div className="flex flex-col space-y-2">
          {leftSide.map((entry, index) => (
            <div key={index} className="flex items-center gap-2">
              <div 
                className={`w-3 h-3 rounded-full border border-white shadow-sm [background-color:${entry.color}]`}/>      
              <span className="text-sm font-medium text-gray-900">{entry.name}</span>
              <span className="text-xs text-gray-600">({entry.value})</span>
            </div>
          ))}
        </div>
        
        {/* Pie Chart */}
        <div className="flex-shrink-0">
          <ResponsiveContainer width={300} height={350}>
            <RechartsPieChart>
              <Pie
                data={data}
                cx="50%"
                cy="50%"
                outerRadius={120}
                fill="#8884d8"
                dataKey="value"
                stroke="#fff"
                strokeWidth={2}
              >
                {data.map((entry, index) => (
                  <Cell key={`cell-${index}`} fill={entry.color} />
                ))}
              </Pie>
              <Tooltip 
                formatter={(value, name, props) => [
                  `${value} leaves (${((props.payload.value / data.reduce((acc, item) => acc + item.value, 0)) * 100).toFixed(1)}%)`,
                  'Count'
                ]}
                labelFormatter={(label) => `Base: ${label}`}
              />
            </RechartsPieChart>
          </ResponsiveContainer>
        </div>
        
        {/* Right Legend */}
        <div className="flex flex-col space-y-2">
          {rightSide.map((entry, index) => (
            <div key={index} className="flex items-center gap-2">
              <div
                className={`w-3 h-3 rounded-full border border-white shadow-sm [background-color:${entry.color}]`}
              ></div>
              <span className="text-sm font-medium text-gray-900">{entry.name}</span>
              <span className="text-xs text-gray-600">({entry.value})</span>
            </div>
          ))}
        </div>
      </div>
    );
  };

  // New chart rendering functions
  const renderStatusBreakdownChart = () => {
    const data = getStatusBreakdownData();
    const midpoint = Math.ceil(data.length / 2);
    const leftSide = data.slice(0, midpoint);
    const rightSide = data.slice(midpoint);
    
    return (
      <div className="flex items-center justify-center gap-6">
        <div className="flex flex-col space-y-2">
          {leftSide.map((entry, index) => (
            <div key={index} className="flex items-center gap-2">
              <div
                className={`w-3 h-3 rounded-full border border-white shadow-sm [background-color:${entry.color}]`}
              />
              <span className="text-sm font-medium text-gray-900">{entry.name}</span>
              <span className="text-xs text-gray-600">({entry.value})</span>
            </div>
          ))}
        </div>
        
        <div className="flex-shrink-0">
          <ResponsiveContainer width={300} height={350}>
            <RechartsPieChart>
              <Pie
                data={data}
                cx="50%"
                cy="50%"
                outerRadius={120}
                fill="#8884d8"
                dataKey="value"
                stroke="#fff"
                strokeWidth={2}
              >
                {data.map((entry, index) => (
                  <Cell key={`cell-${index}`} fill={entry.color} />
                ))}
              </Pie>
              <Tooltip 
                formatter={(value, name, props) => [
                  `${value} applications (${((props.payload.value / data.reduce((acc, item) => acc + item.value, 0)) * 100).toFixed(1)}%)`,
                  'Count'
                ]}
                labelFormatter={(label) => `Status: ${label}`}
              />
            </RechartsPieChart>
          </ResponsiveContainer>
        </div>
        
        <div className="flex flex-col space-y-2">
          {rightSide.map((entry, index) => (
            <div key={index} className="flex items-center gap-2">
              <div
              className={`w-3 h-3 rounded-full border border-white shadow-sm [background-color:${entry.color}]`}
            ></div>
              <span className="text-sm font-medium text-gray-900">{entry.name}</span>
              <span className="text-xs text-gray-600">({entry.value})</span>
            </div>
          ))}
        </div>
      </div>
    );
  };

  const renderLeadTimeChart = () => (
    <ResponsiveContainer width="100%" height={350}>
      <RechartsBarChart data={getLeaveApplicationLeadTimeData()}>
        <CartesianGrid strokeDasharray="3 3" />
        <XAxis dataKey="range" />
        <YAxis />
        <Tooltip formatter={(value, name) => [value, "Application Count"]} />
        <Legend />
        <Bar dataKey="count" fill="#8b5cf6" radius={[4, 4, 0, 0]} />
      </RechartsBarChart>
    </ResponsiveContainer>
  );

  const renderDayOfWeekChart = () => (
    <ResponsiveContainer width="100%" height={350}>
      <RechartsBarChart data={getDayOfWeekLeaveData()}>
        <CartesianGrid strokeDasharray="3 3" />
        <XAxis dataKey="day" />
        <YAxis />
        <Tooltip formatter={(value, name) => [value, "Leave Count"]} />
        <Legend />
        <Bar dataKey="leaves" fill="#06b6d4" radius={[4, 4, 0, 0]} />
      </RechartsBarChart>
    </ResponsiveContainer>
  );

  const renderBaseAverageDurationChart = () => (
    <ResponsiveContainer width="100%" height={350}>
      <RechartsBarChart data={getBaseAverageDurationData()}>
        <CartesianGrid strokeDasharray="3 3" />
        <XAxis dataKey="base" />
        <YAxis />
        <Tooltip 
          formatter={(value, name, props) => [
            name === "avgDuration" ? `${value} days` : value,
            name === "avgDuration" ? "Average Duration" : "Total Leaves"
          ]}
        />
        <Legend />
        <Bar dataKey="avgDuration" fill="#f97316" radius={[4, 4, 0, 0]} />
      </RechartsBarChart>
    </ResponsiveContainer>
  );



  const LoadingSpinner = () => (
    <div className="flex flex-col items-center justify-center h-64 space-y-4">
      <div className="relative">
        <div className="w-16 h-16 border-4 border-indigo-primary/30 border-t-indigo-primary rounded-full animate-spin"></div>
        <div className="absolute inset-0 flex items-center justify-center">
          <div className="w-8 h-8 bg-indigo-gradient rounded-full animate-pulse"></div>
        </div>
      </div>
      <div className="text-center">
        <p className="text-indigo-primary font-semibold">Loading AI Analytics...</p>
        <p className="text-sm text-muted-foreground">Please wait a moment.</p>
      </div>
    </div>
  );

  return (
    <Layout userRole="Admin" onLogout={handleLogout}>
      <div className="space-y-4">
        {/* Header Card */}
        <div className="card-glass shadow-indigo border border-indigo-100">
          <div className="p-1">
            <div className="flex flex-col lg:flex-row lg:items-center justify-between gap-4">
              <div className="flex items-center gap-3">
                <div className="w-10 h-10 bg-indigo-gradient rounded-xl flex items-center justify-center shadow-indigo">
                  <Brain className="h-6 w-6 text-white" />
                </div>
                <div>
                  <h1 className="text-3xl font-bold text-gray-900 mb-1">
                    URTI AI Analytics
                  </h1>
                  <p className="text-gray-600">
                    Advanced analytics and insights for URTI management
                  </p>
                </div>
              </div>
            </div>
          </div>
        </div>

        {/* Search Bar */}
        <div className="glass rounded-2xl shadow-indigo p-4 animate-fade-in-up animate-stagger-2">
          <div className="relative">
            <div className="absolute left-4 top-1/2 transform -translate-y-1/2">
              <Search className="w-5 h-5 text-gray-400" />
            </div>
            <input
              value={searchKeyword}
              onChange={(e) => setSearchKeyword(e.target.value)}
              placeholder="Search by IGA code or employee name for specific analytics..."
              className="w-full pl-12 pr-4 py-3 text-sm bg-white/90 border-2 border-indigo-primary/20 rounded-lg focus:outline-none focus:ring-indigo-primary/50 focus:border-indigo-primary transition-all"
            />
            {searchKeyword && (
              <button
                onClick={() => setSearchKeyword("")}
                className="absolute right-3 top-1/2 transform -translate-y-1/2 p-1 text-gray-400 hover:text-red-500 transition-colors duration-200"
              >
                <svg className="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            )}
          </div>
          {searchKeyword && (
            <p className="mt-2 text-sm text-indigo-600">
              Showing analytics for: <span className="font-semibold">{searchKeyword}</span>
              {filteredData.length > 0 && ` (${filteredData.length} records found)`}
            </p>
          )}
        </div>

        {/* Analytics Dashboard */}
        {isLoading ? (
          <div className="glass rounded-2xl shadow-indigo-lg overflow-hidden animate-fade-in-up animate-stagger-3">
            <LoadingSpinner />
          </div>
        ) : filteredData.length > 0 ? (
          <div className="space-y-6">
            {/* Search Result Banner */}
            {searchKeyword && (
              <div className="glass rounded-2xl shadow-indigo p-4 animate-fade-in-up animate-stagger-3">
                <div className="p-3 bg-blue-50 border border-blue-200 rounded-lg">
                  <p className="text-sm text-blue-800">
                    <strong>Filtered View:</strong> Showing data for "{searchKeyword}" 
                    ({filteredData.length} of {attendances.length} total records)
                  </p>
                </div>
              </div>
            )}

            {/* Row 1: Duration Patterns + Base Distribution */}
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
              {/* Duration Patterns Chart */}
              <div className="glass rounded-2xl shadow-indigo-lg overflow-hidden animate-fade-in-up animate-stagger-4">
                <div className="p-6">
                  <div className="mb-4">
                    <h3 className="text-xl font-bold text-gray-900 mb-2 flex items-center gap-2">
                      <BarChart3 className="w-5 h-5 text-indigo-600" />
                      Leave Duration Patterns
                    </h3>
                    <p className="text-sm text-gray-600">Analysis of leave duration ranges and their frequency</p>
                  </div>
                  <div className="bg-white/50 rounded-lg p-4 border border-indigo-100">
                    {renderDurationChart()}
                  </div>
                </div>
              </div>

              {/* Base Distribution Chart */}
              <div className="glass rounded-2xl shadow-indigo-lg overflow-hidden animate-fade-in-up animate-stagger-5">
                <div className="p-6">
                  <div className="mb-4">
                    <h3 className="text-xl font-bold text-gray-900 mb-2 flex items-center gap-2">
                      <PieChart className="w-5 h-5 text-green-600" />
                      Leave Distribution by Base
                    </h3>
                    <p className="text-sm text-gray-600">Distribution of leave requests across different bases</p>
                  </div>
                  <div className="bg-white/50 rounded-lg p-4 border border-indigo-100">
                    {renderBaseChart()}
                  </div>
                </div>
              </div>
            </div>

            {/* Row 2: Status Breakdown + Application Lead Time */}
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
              {/* Status Breakdown Chart */}
              <div className="glass rounded-2xl shadow-indigo-lg overflow-hidden animate-fade-in-up animate-stagger-6">
                <div className="p-6">
                  <div className="mb-4">
                    <h3 className="text-xl font-bold text-gray-900 mb-2 flex items-center gap-2">
                      <PieChart className="w-5 h-5 text-purple-600" />
                      Leave Status Breakdown
                    </h3>
                    <p className="text-sm text-gray-600">Percentage of approved, pending, and rejected applications</p>
                  </div>
                  <div className="bg-white/50 rounded-lg p-4 border border-indigo-100">
                    {renderStatusBreakdownChart()}
                  </div>
                </div>
              </div>

              {/* Application Lead Time Chart */}
              <div className="glass rounded-2xl shadow-indigo-lg overflow-hidden animate-fade-in-up animate-stagger-7">
                <div className="p-6">
                  <div className="mb-4">
                    <h3 className="text-xl font-bold text-gray-900 mb-2 flex items-center gap-2">
                      <BarChart3 className="w-5 h-5 text-purple-600" />
                      Application Lead Time
                    </h3>
                    <p className="text-sm text-gray-600">How early employees apply for leave before the start date</p>
                  </div>
                  <div className="bg-white/50 rounded-lg p-4 border border-indigo-100">
                    {renderLeadTimeChart()}
                  </div>
                </div>
              </div>
            </div>

            {/* Row 3: Seasonal Trends (Full Width) */}
            <div className="glass rounded-2xl shadow-indigo-lg overflow-hidden animate-fade-in-up animate-stagger-8">
              <div className="p-6">
                <div className="mb-4">
                  <h3 className="text-xl font-bold text-gray-900 mb-2 flex items-center gap-2">
                    <LineChart className="w-5 h-5 text-blue-600" />
                    Seasonal Leave Trends
                  </h3>
                  <p className="text-sm text-gray-600">Monthly trends showing peak leave periods throughout the year</p>
                </div>
                <div className="bg-white/50 rounded-lg p-4 border border-indigo-100">
                  {renderSeasonalChart()}
                </div>
              </div>
            </div>

            {/* Row 4: Day of Week + Base Average Duration */}
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
              {/* Day of Week Leave Start Chart */}
              <div className="glass rounded-2xl shadow-indigo-lg overflow-hidden animate-fade-in-up animate-stagger-9">
                <div className="p-6">
                  <div className="mb-4">
                    <h3 className="text-xl font-bold text-gray-900 mb-2 flex items-center gap-2">
                      <Calendar className="w-5 h-5 text-cyan-600" />
                      Leave Start by Day of Week
                    </h3>
                    <p className="text-sm text-gray-600">Which days employees prefer to start their leave</p>
                  </div>
                  <div className="bg-white/50 rounded-lg p-4 border border-indigo-100">
                    {renderDayOfWeekChart()}
                  </div>
                </div>
              </div>

              {/* Base Average Duration Chart */}
              <div className="glass rounded-2xl shadow-indigo-lg overflow-hidden animate-fade-in-up animate-stagger-10">
                <div className="p-6">
                  <div className="mb-4">
                    <h3 className="text-xl font-bold text-gray-900 mb-2 flex items-center gap-2">
                      <BarChart3 className="w-5 h-5 text-orange-600" />
                      Average Leave Duration by Base
                    </h3>
                    <p className="text-sm text-gray-600">Compare average leave lengths across different bases</p>
                  </div>
                  <div className="bg-white/50 rounded-lg p-4 border border-indigo-100">
                    {renderBaseAverageDurationChart()}
                  </div>
                </div>
              </div>
            </div>
          </div>
        ) : (
          <div className="glass rounded-2xl shadow-indigo-lg overflow-hidden animate-fade-in-up animate-stagger-3">
            <div className="text-center py-12">
              <Search className="h-16 w-16 text-gray-400 mx-auto mb-4" />
              <h3 className="text-lg font-semibold text-gray-900 mb-2">No Data Found</h3>
              <p className="text-gray-600">
                {searchKeyword 
                  ? `No records found for "${searchKeyword}". Try a different search term.`
                  : "No leave data available to display charts."
                }
              </p>
            </div>
          </div>
        )}

        {/* Stats Summary */}
        {!isLoading && attendances.length > 0 && (
          <div className="glass rounded-2xl shadow-indigo p-4 animate-fade-in-up animate-stagger-5">
            <h3 className="text-lg font-semibold text-gray-900 mb-4">
              {searchKeyword ? "Filtered" : "Overall"} Statistics
            </h3>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
              <div className="text-center p-4 bg-white/50 rounded-lg border border-indigo-100">
                <div className="text-2xl font-bold text-indigo-600">{filteredData.length}</div>
                <div className="text-sm text-gray-600">Total Records</div>
              </div>
              <div className="text-center p-4 bg-white/50 rounded-lg border border-indigo-100">
                <div className="text-2xl font-bold text-green-600">
                  {filteredData.filter(d => d.status === "Approved").length}
                </div>
                <div className="text-sm text-gray-600">Approved</div>
              </div>
              <div className="text-center p-4 bg-white/50 rounded-lg border border-indigo-100">
                <div className="text-2xl font-bold text-yellow-600">
                  {filteredData.filter(d => d.status === "Pending").length}
                </div>
                <div className="text-sm text-gray-600">Pending</div>
              </div>
              <div className="text-center p-4 bg-white/50 rounded-lg border border-indigo-100">
                <div className="text-2xl font-bold text-blue-600">
                  {Math.round(filteredData.reduce((acc, d) => acc + d.duration, 0) / filteredData.length) || 0}
                </div>
                <div className="text-sm text-gray-600">Avg. Duration</div>
              </div>
            </div>
          </div>
        )}
      </div>
    </Layout>
  );
};

export default UrtiAIAnalyticsAdminPage;
