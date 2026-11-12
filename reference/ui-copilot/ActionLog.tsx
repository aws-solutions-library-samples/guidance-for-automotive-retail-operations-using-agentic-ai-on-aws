"use client";

import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { useEffect, useState } from 'react';
import { Bot, Target, MessageSquare, Swords, Gift, Shield, FileText } from 'lucide-react';

interface ActionLogProps {
  customerId: string;
}

type IconType = 'bot' | 'target' | 'message' | 'sword' | 'gift' | 'shield' | 'file';

interface LogEntry {
  timestamp: string;
  iconType: IconType;
  message: string;
  status: 'success' | 'pending' | 'error';
}

export function ActionLog({ customerId }: ActionLogProps) {
  const [logs, setLogs] = useState<LogEntry[]>([]);

  useEffect(() => {
    // Initialize with sample logs
    const initialLogs: LogEntry[] = [
      {
        timestamp: new Date().toLocaleTimeString(),
        iconType: 'bot',
        message: 'Analyzed customer profile - High intent detected',
        status: 'success'
      },
      {
        timestamp: new Date(Date.now() - 4000).toLocaleTimeString(),
        iconType: 'target',
        message: 'Generated 3 vehicle recommendations with 96% confidence',
        status: 'success'
      },
      {
        timestamp: new Date(Date.now() - 10000).toLocaleTimeString(),
        iconType: 'message',
        message: 'Detected objection: "Price too high" - Preparing response',
        status: 'success'
      },
      {
        timestamp: new Date(Date.now() - 14000).toLocaleTimeString(),
        iconType: 'sword',
        message: 'Loaded competitive intelligence for Kestrel Trek 4',
        status: 'success'
      },
      {
        timestamp: new Date(Date.now() - 19000).toLocaleTimeString(),
        iconType: 'gift',
        message: 'Identified 2 applicable incentives worth $1,250',
        status: 'success'
      },
      {
        timestamp: new Date(Date.now() - 22000).toLocaleTimeString(),
        iconType: 'shield',
        message: 'Calculated F&I product recommendations',
        status: 'success'
      }
    ];
    setLogs(initialLogs);
  }, [customerId]);

  const getIcon = (iconType: IconType) => {
    const iconClass = "h-4 w-4";
    switch (iconType) {
      case 'bot':
        return <Bot className={`${iconClass} text-blue-500`} />;
      case 'target':
        return <Target className={`${iconClass} text-purple-500`} />;
      case 'message':
        return <MessageSquare className={`${iconClass} text-orange-500`} />;
      case 'sword':
        return <Swords className={`${iconClass} text-red-500`} />;
      case 'gift':
        return <Gift className={`${iconClass} text-green-500`} />;
      case 'shield':
        return <Shield className={`${iconClass} text-cyan-500`} />;
      case 'file':
        return <FileText className={`${iconClass} text-gray-500`} />;
      default:
        return <Bot className={`${iconClass} text-gray-500`} />;
    }
  };

  const getStatusColor = (status: string) => {
    switch (status) {
      case 'success':
        return 'text-green-600';
      case 'pending':
        return 'text-yellow-600';
      case 'error':
        return 'text-red-600';
      default:
        return 'text-gray-600';
    }
  };

  const getStatusBadge = (status: string) => {
    switch (status) {
      case 'success':
        return <Badge variant="outline" className="bg-green-500/10 text-green-600 border-green-500/20">Complete</Badge>;
      case 'pending':
        return <Badge variant="outline" className="bg-yellow-500/10 text-yellow-600 border-yellow-500/20">Pending</Badge>;
      case 'error':
        return <Badge variant="outline" className="bg-red-500/10 text-red-600 border-red-500/20">Error</Badge>;
      default:
        return null;
    }
  };

  return (
    <Card>
      <CardHeader className="border-b">
        <div className="flex items-center justify-between">
          <CardTitle className="text-lg flex items-center gap-2">
            <FileText className="h-5 w-5 text-slate-500" />
            AI Action Log
          </CardTitle>
          <Button size="sm" variant="ghost">Clear</Button>
        </div>
      </CardHeader>
      <CardContent className="pt-4">
        <div className="space-y-2">
          {logs.map((log, idx) => (
            <div key={idx} className="flex items-center gap-4 p-3 rounded-lg hover:bg-muted/50 transition-colors">
              <span className="text-xs text-muted-foreground font-mono tabular-nums w-20">
                {log.timestamp}
              </span>
              <div className="flex-shrink-0">
                {getIcon(log.iconType)}
              </div>
              <span className="flex-1 text-sm">{log.message}</span>
              {getStatusBadge(log.status)}
            </div>
          ))}
        </div>
      </CardContent>
    </Card>
  );
}
