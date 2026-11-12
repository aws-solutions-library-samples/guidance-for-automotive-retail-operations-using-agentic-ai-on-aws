"use client";

import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { ScrollArea } from '@/components/ui/scroll-area';
import {
  Activity, Bot, Database, CheckCircle, XCircle, Clock,
  ChevronDown, ChevronUp, Loader2, Brain
} from 'lucide-react';
import { useState } from 'react';
import { TraceEvent } from '@/lib/agentcore-service';

interface ReasoningTraceProps {
  trace: TraceEvent[];
  isLoading: boolean;
}

function getStepIcon(event: TraceEvent) {
  if (event.type === 'a2a_call_start' || event.type === 'a2a_call_end') {
    return <Bot className="h-4 w-4 text-purple-500" />;
  }
  return <Database className="h-4 w-4 text-blue-500" />;
}

function getStepColor(event: TraceEvent): string {
  if (event.type === 'a2a_call_start' || event.type === 'a2a_call_end') {
    return 'border-purple-500/30 bg-purple-500/5';
  }
  return 'border-blue-500/30 bg-blue-500/5';
}

function formatDuration(ms?: number): string {
  if (!ms) return '';
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

function formatToolName(name: string): string {
  return name
    .replace(/^call_/, '')
    .replace(/_agent$/, '')
    .replace(/_/g, ' ')
    .replace(/\b\w/g, c => c.toUpperCase());
}

// Pair start/end events into steps
function buildSteps(trace: TraceEvent[]) {
  const steps: Array<{
    tool: string;
    isA2A: boolean;
    input?: string;
    output?: string;
    duration?: number;
    status?: string;
    startTime: string;
  }> = [];

  // Track pending steps per tool as a stack (LIFO) to handle repeated calls
  const pending = new Map<string, number[]>();

  for (const event of trace) {
    if (event.type === 'tool_start' || event.type === 'a2a_call_start') {
      const idx = steps.length;
      const stack = pending.get(event.tool) || [];
      stack.push(idx);
      pending.set(event.tool, stack);
      steps.push({
        tool: event.tool,
        isA2A: event.type === 'a2a_call_start',
        input: event.input_summary,
        startTime: event.timestamp,
      });
    } else if (event.type === 'tool_end' || event.type === 'a2a_call_end') {
      const stack = pending.get(event.tool);
      if (stack && stack.length > 0) {
        const idx = stack.shift()!;
        steps[idx].output = event.output_summary;
        steps[idx].duration = event.duration_ms;
        steps[idx].status = event.status;
        if (stack.length === 0) pending.delete(event.tool);
      }
    }
  }

  return { steps, hasPending: pending.size > 0 };
}

export function ReasoningTrace({ trace, isLoading }: ReasoningTraceProps) {
  const [expandedStep, setExpandedStep] = useState<number | null>(null);
  const { steps, hasPending } = buildSteps(trace);

  if (!isLoading && steps.length === 0) {
    return null;
  }

  return (
    <Card>
      <CardHeader className="border-b py-3">
        <div className="flex items-center justify-between">
          <CardTitle className="text-sm flex items-center gap-2">
            <Brain className="h-4 w-4 text-indigo-500" />
            Agent Reasoning Trace
          </CardTitle>
          <div className="flex items-center gap-2">
            {steps.length > 0 && (
              <Badge variant="outline" className="text-xs">
                {steps.length} step{steps.length !== 1 ? 's' : ''}
              </Badge>
            )}
            {isLoading && (
              <Badge variant="outline" className="bg-blue-500/10 text-blue-600 border-blue-500/20 text-xs">
                <Loader2 className="h-3 w-3 mr-1 animate-spin" />
                Running
              </Badge>
            )}
          </div>
        </div>
      </CardHeader>
      <CardContent className="p-0">
        <ScrollArea className="max-h-[400px]">
          <div className="p-4 space-y-2">
            {steps.map((step, idx) => {
              const isExpanded = expandedStep === idx;
              const isComplete = !!step.status;
              const isRunning = !isComplete && (isLoading || hasPending);

              return (
                <div
                  key={idx}
                  className={`border rounded-lg p-3 transition-all ${
                    isRunning ? 'border-blue-500/50 bg-blue-500/5 animate-pulse' : getStepColor({ type: step.isA2A ? 'a2a_call_end' : 'tool_end', tool: step.tool, timestamp: '' })
                  }`}
                >
                  <div
                    className="flex items-center gap-3 cursor-pointer"
                    onClick={() => setExpandedStep(isExpanded ? null : idx)}
                  >
                    {/* Step number */}
                    <div className={`h-6 w-6 rounded-full flex items-center justify-center text-xs font-bold ${
                      isComplete && step.status === 'success'
                        ? 'bg-green-500/20 text-green-600'
                        : isComplete && step.status === 'error'
                        ? 'bg-red-500/20 text-red-600'
                        : 'bg-blue-500/20 text-blue-600'
                    }`}>
                      {idx + 1}
                    </div>

                    {/* Icon */}
                    {step.isA2A ? <Bot className="h-4 w-4 text-purple-500" /> : <Database className="h-4 w-4 text-blue-500" />}

                    {/* Tool name */}
                    <div className="flex-1">
                      <span className="text-sm font-medium">{formatToolName(step.tool)}</span>
                      {step.isA2A && (
                        <Badge variant="secondary" className="ml-2 text-[10px] py-0">A2A</Badge>
                      )}
                    </div>

                    {/* Duration */}
                    {step.duration && (
                      <Badge variant="outline" className="text-xs">
                        <Clock className="h-3 w-3 mr-1" />
                        {formatDuration(step.duration)}
                      </Badge>
                    )}

                    {/* Status */}
                    {isComplete ? (
                      step.status === 'success' ? (
                        <CheckCircle className="h-4 w-4 text-green-500" />
                      ) : (
                        <XCircle className="h-4 w-4 text-red-500" />
                      )
                    ) : isRunning ? (
                      <Loader2 className="h-4 w-4 text-blue-500 animate-spin" />
                    ) : null}

                    {/* Expand */}
                    {isExpanded ? <ChevronUp className="h-4 w-4 text-muted-foreground" /> : <ChevronDown className="h-4 w-4 text-muted-foreground" />}
                  </div>

                  {/* Expanded detail */}
                  {isExpanded && (
                    <div className="mt-3 space-y-2 pl-9">
                      {step.input && (
                        <div>
                          <div className="text-[10px] font-semibold text-muted-foreground uppercase mb-1">Input</div>
                          <pre className="text-xs bg-background/80 rounded p-2 overflow-x-auto whitespace-pre-wrap">
                            {step.input}
                          </pre>
                        </div>
                      )}
                      {step.output && (
                        <div>
                          <div className="text-[10px] font-semibold text-muted-foreground uppercase mb-1">Output</div>
                          <pre className="text-xs bg-background/80 rounded p-2 overflow-x-auto whitespace-pre-wrap max-h-[200px] overflow-y-auto">
                            {step.output}
                          </pre>
                        </div>
                      )}
                    </div>
                  )}
                </div>
              );
            })}

            {isLoading && steps.length === 0 && (
              <div className="text-center py-6 text-muted-foreground">
                <Loader2 className="h-6 w-6 mx-auto animate-spin mb-2" />
                <p className="text-sm">Agent is thinking...</p>
              </div>
            )}
          </div>
        </ScrollArea>
      </CardContent>
    </Card>
  );
}
