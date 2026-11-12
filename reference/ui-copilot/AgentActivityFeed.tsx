"use client";

import { useState } from 'react';
import { Badge } from '@/components/ui/badge';
import { ScrollArea } from '@/components/ui/scroll-area';
import {
  Database, Search, Save, Users, Car, Shield, UserCircle,
  CheckCircle, XCircle, Loader2, Clock, Zap, Bot,
  ChevronDown, ChevronUp,
} from 'lucide-react';
import { TraceEvent } from '@/lib/agentcore-service';

interface AgentActivityFeedProps {
  trace: TraceEvent[];
  isLoading: boolean;
}

const TOOL_CONFIG: Record<string, { icon: typeof Database; color: string; label: string }> = {
  get_my_customers: { icon: Users, color: 'blue', label: 'Loading Customer Portfolio' },
  get_dms_customer_records: { icon: UserCircle, color: 'blue', label: 'Fetching Customer Details' },
  get_my_prospects: { icon: Search, color: 'blue', label: 'Checking Existing Prospects' },
  save_prospect: { icon: Save, color: 'green', label: 'Saving Prospect' },
  update_prospect_status: { icon: Save, color: 'green', label: 'Updating Prospect Status' },
  identify_lease_expiration_prospects: { icon: Zap, color: 'amber', label: 'Analyzing Lease Expirations' },
  enrich_lease_prospect: { icon: Database, color: 'purple', label: 'Enriching Lease Data' },
  score_lease_prospect: { icon: Zap, color: 'amber', label: 'Scoring Lease Prospect' },
  identify_high_service_frequency_prospects: { icon: Zap, color: 'amber', label: 'Analyzing Service Frequency' },
  enrich_service_prospect: { icon: Database, color: 'purple', label: 'Enriching Service Data' },
  score_service_prospect: { icon: Zap, color: 'amber', label: 'Scoring Service Prospect' },
  identify_aging_vehicle_prospects: { icon: Car, color: 'amber', label: 'Analyzing Aging Vehicles' },
  enrich_aging_vehicle_prospect: { icon: Database, color: 'purple', label: 'Enriching Vehicle Data' },
  score_aging_vehicle_prospect: { icon: Zap, color: 'amber', label: 'Scoring Aging Vehicle' },
  // Supervisor A2A tools
  call_prospecting_agent: { icon: Bot, color: 'purple', label: 'Calling Prospecting Agent' },
  call_recommendation_agent: { icon: Car, color: 'purple', label: 'Calling Recommendation Agent' },
  call_objection_handler_agent: { icon: Shield, color: 'purple', label: 'Calling Objection Handler' },
  get_customer_360_view: { icon: UserCircle, color: 'blue', label: 'Loading Customer 360' },
  get_lead_pipeline_summary: { icon: Users, color: 'blue', label: 'Loading Pipeline Summary' },
  search_customers: { icon: Search, color: 'blue', label: 'Searching Customers' },
};

const COLOR_CLASSES = {
  blue: { bg: 'bg-blue-500/10', text: 'text-blue-600', border: 'border-blue-500/20', dot: 'bg-blue-500' },
  green: { bg: 'bg-green-500/10', text: 'text-green-600', border: 'border-green-500/20', dot: 'bg-green-500' },
  amber: { bg: 'bg-amber-500/10', text: 'text-amber-600', border: 'border-amber-500/20', dot: 'bg-amber-500' },
  purple: { bg: 'bg-purple-500/10', text: 'text-purple-600', border: 'border-purple-500/20', dot: 'bg-purple-500' },
};

function getConfig(toolName: string) {
  return TOOL_CONFIG[toolName] || { icon: Database, color: 'blue', label: toolName.replace(/_/g, ' ') };
}

function formatDuration(ms?: number): string {
  if (!ms) return '';
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

// Build completed steps from paired start/end events
function buildSteps(trace: TraceEvent[]) {
  const steps: Array<{
    tool: string;
    config: typeof TOOL_CONFIG[string];
    colors: typeof COLOR_CLASSES[keyof typeof COLOR_CLASSES];
    duration?: number;
    status?: string;
    inputSummary?: string;
    outputSummary?: string;
    isRunning: boolean;
  }> = [];

  const pending = new Map<string, number[]>();

  for (const event of trace) {
    if (event.type === 'tool_start' || event.type === 'a2a_call_start') {
      const idx = steps.length;
      const stack = pending.get(event.tool) || [];
      stack.push(idx);
      pending.set(event.tool, stack);
      const config = getConfig(event.tool);
      const colors = COLOR_CLASSES[config.color as keyof typeof COLOR_CLASSES] || COLOR_CLASSES.blue;
      steps.push({ tool: event.tool, config, colors, isRunning: true, inputSummary: event.input_summary });
    } else if (event.type === 'tool_end' || event.type === 'a2a_call_end') {
      const stack = pending.get(event.tool);
      if (stack && stack.length > 0) {
        const idx = stack.shift()!;
        steps[idx].duration = event.duration_ms;
        steps[idx].status = event.status;
        steps[idx].outputSummary = event.output_summary;
        steps[idx].isRunning = false;
        if (stack.length === 0) pending.delete(event.tool);
      }
    }
  }

  return steps;
}

function StepRow({ step }: { step: ReturnType<typeof buildSteps>[number] }) {
  const [expanded, setExpanded] = useState(false);
  const Icon = step.config.icon;
  const hasDetail = step.inputSummary || step.outputSummary;

  return (
    <div>
      <div
        className={`flex items-center gap-3 px-3 py-2 rounded-lg transition-all ${
          hasDetail && !step.isRunning ? 'cursor-pointer' : ''
        } ${
          step.isRunning
            ? `${step.colors.bg} ${step.colors.border} border animate-pulse`
            : 'hover:bg-gray-50'
        }`}
        onClick={() => hasDetail && !step.isRunning && setExpanded(!expanded)}
      >
        {/* Status dot */}
        <div className="flex-shrink-0">
          {step.isRunning ? (
            <Loader2 className={`h-4 w-4 ${step.colors.text} animate-spin`} />
          ) : step.status === 'success' ? (
            <CheckCircle className="h-4 w-4 text-green-500" />
          ) : (
            <XCircle className="h-4 w-4 text-red-500" />
          )}
        </div>

        {/* Icon */}
        <div className={`h-7 w-7 rounded-lg ${step.colors.bg} flex items-center justify-center flex-shrink-0`}>
          <Icon className={`h-3.5 w-3.5 ${step.colors.text}`} />
        </div>

        {/* Label */}
        <div className="flex-1 min-w-0">
          <div className={`text-sm font-medium text-gray-700 ${expanded ? '' : 'truncate'}`} title={step.config.label}>
            {step.config.label}
          </div>
          {!expanded && step.outputSummary && step.status === 'success' && (
            <div className="text-[11px] text-gray-400 truncate mt-0.5">
              {step.outputSummary.substring(0, 80)}
            </div>
          )}
        </div>

        {/* Duration */}
        {step.duration !== undefined && step.duration > 0 && (
          <Badge variant="outline" className="text-[10px] flex-shrink-0">
            <Clock className="h-2.5 w-2.5 mr-0.5" />
            {formatDuration(step.duration)}
          </Badge>
        )}

        {/* Expand indicator */}
        {hasDetail && !step.isRunning && (
          <div className="flex-shrink-0 text-gray-300">
            {expanded ? <ChevronUp className="h-3.5 w-3.5" /> : <ChevronDown className="h-3.5 w-3.5" />}
          </div>
        )}
      </div>

      {/* Expanded detail */}
      {expanded && (
        <div className="ml-[52px] mr-3 mb-2 mt-1 space-y-1.5">
          <div className="text-[10px] text-gray-400 font-mono">{step.tool}</div>
          {step.inputSummary && (
            <div className="rounded-lg bg-gray-50 p-2.5">
              <p className="text-[10px] font-semibold text-gray-500 uppercase tracking-wide mb-1">Input</p>
              <pre className="text-[11px] text-gray-600 whitespace-pre-wrap break-all font-mono leading-relaxed">
                {(() => {
                  try { return JSON.stringify(JSON.parse(step.inputSummary), null, 2); }
                  catch { return step.inputSummary; }
                })()}
              </pre>
            </div>
          )}
          {step.outputSummary && (
            <div className={`rounded-lg p-2.5 ${step.status === 'error' ? 'bg-red-50' : 'bg-green-50'}`}>
              <p className={`text-[10px] font-semibold uppercase tracking-wide mb-1 ${step.status === 'error' ? 'text-red-500' : 'text-green-600'}`}>
                Output
              </p>
              <pre className={`text-[11px] whitespace-pre-wrap break-all font-mono leading-relaxed ${step.status === 'error' ? 'text-red-600' : 'text-gray-600'}`}>
                {(() => {
                  try { return JSON.stringify(JSON.parse(step.outputSummary), null, 2); }
                  catch { return step.outputSummary; }
                })()}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export function AgentActivityFeed({ trace, isLoading }: AgentActivityFeedProps) {
  const steps = buildSteps(trace);

  if (!isLoading && steps.length === 0) return null;

  return (
    <div className="border rounded-xl bg-white overflow-hidden">
      <div className="flex items-center justify-between px-4 py-3 border-b bg-gray-50/80">
        <div className="flex items-center gap-2">
          <Zap className="h-4 w-4 text-amber-500" />
          <span className="text-sm font-semibold text-gray-700">Agent Activity</span>
        </div>
        <div className="flex items-center gap-2">
          {steps.length > 0 && (
            <Badge variant="outline" className="text-[10px]">
              {steps.filter(s => s.status === 'success').length}/{steps.length} complete
            </Badge>
          )}
          {isLoading && (
            <div className="flex items-center gap-1 text-blue-600">
              <Loader2 className="h-3 w-3 animate-spin" />
              <span className="text-[10px] font-medium">Live</span>
            </div>
          )}
        </div>
      </div>

      <ScrollArea className="max-h-[400px]">
        <div className="p-3 space-y-1">
          {steps.map((step, idx) => (
            <StepRow key={idx} step={step} />
          ))}

          {isLoading && steps.length === 0 && (
            <div className="flex items-center gap-3 px-3 py-4 text-gray-400">
              <Loader2 className="h-4 w-4 animate-spin" />
              <span className="text-sm">Agent is starting up...</span>
            </div>
          )}
        </div>
      </ScrollArea>
    </div>
  );
}
