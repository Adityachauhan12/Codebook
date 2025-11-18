import React from 'react';
import Badge from '../../../components/common/Badge';
import { GroomingCardProps } from '../types';
import { formatDateTime } from '../utils/groomingHelpers';
import Layout from '../../../components/Layout';

const GroomingCard: React.FC<GroomingCardProps> = ({ 
  report, 
  index, 
  onViewDetails 
}) => {
  console.log(report)
  const categories = [
    { 
      category: "uniform", 
      score: report.categories.uniform, 
      label: "Uniform"
    },
    { 
      category: "hygiene", 
      score: report.categories.hygiene, 
      label: "Hygiene"
    },
    { 
      category: "appearance", 
      score: report.categories.appearance, 
      label: "Appearance"
    },
    { 
      category: "attitude", 
      score: report.categories.attitude, 
      label: "Attitude"
    }
  ];

  const handleCardClick = () => {
    if (onViewDetails) {
      onViewDetails(report);
    }
  };

  return (
    
    <div 
      className="bg-white rounded-2xl shadow-lg border border-gray-200 p-6 hover:shadow-xl transition-all duration-500 transform hover:-translate-y-1 cursor-pointer"
      
      onClick={handleCardClick}
    >
      {/* Header Section */}
      <div className="flex flex-col md:flex-row md:items-start justify-between mb-6">
        <div className="flex-1">
          <div className="flex items-center gap-3 mb-2">
            <h3 className="text-lg font-bold text-gray-900">Grooming Assessment</h3>
            {report.mode && (
              <div className="bg-gray-100 text-gray-700 px-2 py-1 rounded-lg text-xs font-medium">
                {report.mode}
              </div>
            )}
          </div>
          <div className="text-gray-600 text-sm mb-1">
            {formatDateTime(report.createdAt)} • Inspector: {report.inspector}
          </div>
        </div>
        <div className="flex items-center gap-4 mt-3 md:mt-0">
          <div className="text-center">
            <div className="text-4xl font-bold mb-1 text-[#000099]">
              {report.overallScore}%
            </div>
            <div className="text-xs text-gray-500">Overall</div>
          </div>
          <Badge status={report.status} size="md">
            {report.status}
          </Badge>
        </div>
      </div>

      {/* Category Scores Grid */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
        {categories.map((item, i) => (
          <div 
            key={i} 
            className="bg-blue-50 rounded-xl p-4 text-center border border-blue-100 hover:shadow-md transition-all duration-300 transform hover:scale-105"
          >
            <div className="text-2xl font-bold mb-1 text-[#000099]">
              {Math.min(Math.max(item.score, 0), 100)}%
            </div>
            <div className="text-gray-600 text-xs font-medium">{item.label}</div>
          </div>
        ))}
      </div>

      {/* Enhanced Scoring Breakdown */}
      {report.scores && (
        <div className="bg-gradient-to-r from-blue-50 to-indigo-50 rounded-xl p-4 border border-blue-200 mb-4">
          <div className="flex items-center justify-between mb-3">
            <h4 className="text-[#000099] font-semibold text-sm">Detailed Scores</h4>
            <div className="text-blue-600 text-xs">
              Total: {Object.values(report.scores).reduce((a, b) => a + b, 0)}/10
            </div>
          </div>
          <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
            {[
              { key: "uniform", label: "Uniform", max: 3 },
              { key: "hairstyle", label: "Hair", max: 2 },
              { key: "makeup", label: "Makeup", max: 2 },
              { key: "nails", label: "Nails", max: 1 },
              { key: "accessories", label: "Access.", max: 2 }
            ].map((item) => (
              <div key={item.key} className="text-center">
                <div className="text-[#000099] font-bold text-sm">
                  {report.scores![item.key as keyof typeof report.scores]}/{item.max}
                </div>
                <div className="text-blue-700 text-xs">{item.label}</div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Feedback Section */}
      {report.feedback && (
        <div className="bg-gray-50 rounded-xl p-4 border border-gray-200 mb-4">
          <span className="text-gray-600 font-medium text-xs block mb-1">Inspector Feedback</span>
          <p className="text-gray-800 text-sm leading-relaxed">{report.feedback}</p>
        </div>
      )}

      {/* Issues Section */}
      {report.issues && report.issues.length > 0 && (
        <div className="bg-red-50 rounded-xl p-4 border border-red-200 mb-4">
          <h4 className="text-red-800 font-semibold text-sm mb-2">Issues Identified</h4>
          <ul className="space-y-1">
            {report.issues.map((issue, idx) => (
              <li key={idx} className="flex items-start gap-2 text-red-700 text-xs">
                <span className="text-red-500 font-bold mt-0.5">{idx + 1}.</span>
                <span className="flex-1">{issue}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Recommendations Section */}
      {report.recommendations && report.recommendations.length > 0 && (
        <div className="bg-green-50 rounded-xl p-4 border border-green-200 mb-4">
          <h4 className="text-green-800 font-semibold text-sm mb-2">Recommendations</h4>
          <ul className="space-y-1">
            {report.recommendations.map((rec, idx) => (
              <li key={idx} className="flex items-start gap-2 text-green-700 text-xs">
                <span className="text-green-500 font-bold mt-0.5">{idx + 1}.</span>
                <span className="flex-1">{rec}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Card Footer */}
      <div className="flex items-center justify-between pt-4 border-t border-gray-200">
        <div className="text-xs text-gray-500">
          Last updated: {formatDateTime(report.updatedAt)}
        </div>
        <button className="text-[#000099] hover:text-blue-800 text-sm font-medium transition-colors duration-300 opacity-0 group-hover:opacity-100">
          View Details
        </button>
      </div>
    </div>
    
  );
};

export default GroomingCard;
