"use client";

import { Badge } from '@/components/ui/badge';
import { Users, UserCircle, Car, Shield, ChevronRight, CheckCircle, Loader2 } from 'lucide-react';
import { TraceEvent } from '@/lib/agentcore-service';

interface PipelineVisualizationProps {
  trace: TraceEvent[];
  isLoading: boolean;
}

const STAGES = [
  { id: 'prospect', label: 'Prospect', icon: Users, tools: ['call_prospecting_agent'] },
  { id: 'customer360', label: 'Customer 360', icon: UserCircle, tools: ['get_customer_360_view', 'search_customers', 'get_lead_pipeline_summary'] },
  { id: 'recommend', label: 'Recommend', icon: Car, tools: ['call_recommendation_agent'] },
  { id: 'objection', label: 'Objection Prep', icon: Shield, tools: ['call_objection_handler_agent'] },
];

function getStageStatus(stage: typeof STAGES[number], trace: TraceEvent[]): 'pending' | 'active' | 'done' {
  const hasStart = trace.some(e =>
    (e.type === 'tool_start' || e.type === 'a2a_call_start') && stage.tools.includes(e.tool)
  );
  const hasEnd = trace.some(e =>
    (e.type === 'tool_end' || e.type === 'a2a_call_end') && stage.tools.includes(e.tool) && e.status === 'success'
  );

  if (hasEnd) return 'done';
  if (hasStart) return 'active';
  return 'pending';
}

export function PipelineVisualization({ trace, isLoading }: PipelineVisualizationProps) {
  if (!isLoading && trace.length === 0) return null;

  return (
    <div className="flex items-center justify-between bg-white border rounded-xl p-4 gap-2">
      {STAGES.map((stage, idx) => {
        const status = getStageStatus(stage, trace);
        const Icon = stage.icon;

        return (
          <div key={stage.id} className="flex items-center gap-2 flex-1">
            <div className={`flex items-center gap-2 px-3 py-2 rounded-lg flex-1 transition-all ${
              status === 'done'
                ? 'bg-green-500/10 border border-green-500/30'
                : status === 'active'
                ? 'bg-blue-500/10 border border-blue-500/30 animate-pulse'
                : 'bg-gray-50 border border-gray-200'
            }`}>
              <div className={`h-8 w-8 rounded-full flex items-center justify-center ${
                status === 'done'
                  ? 'bg-green-500/20'
                  : status === 'active'
                  ? 'bg-blue-500/20'
                  : 'bg-gray-100'
              }`}>
                {status === 'done' ? (
                  <CheckCircle className="h-4 w-4 text-green-600" />
                ) : status === 'active' ? (
                  <Loader2 className="h-4 w-4 text-blue-600 animate-spin" />
                ) : (
                  <Icon className={`h-4 w-4 ${isLoading ? 'text-gray-400' : 'text-gray-300'}`} />
                )}
              </div>
              <span className={`text-xs font-medium ${
                status === 'done' ? 'text-green-700' : status === 'active' ? 'text-blue-700' : 'text-gray-400'
              }`}>
                {stage.label}
              </span>
            </div>
            {idx < STAGES.length - 1 && (
              <ChevronRight className={`h-4 w-4 flex-shrink-0 ${
                status === 'done' ? 'text-green-400' : 'text-gray-300'
              }`} />
            )}
          </div>
        );
      })}
    </div>
  );
}
