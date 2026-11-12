"use client";

import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Car, MessageSquare, Shield, Target, CheckCircle, Loader2, RefreshCw } from 'lucide-react';
import { AgentCoreService } from '@/lib/agentcore-service';
import { useEffect, useState } from 'react';
import { toast } from 'sonner';

interface AIRecommendationsPanelProps {
  customerId: string;
}

export function AIRecommendationsPanel({ customerId }: AIRecommendationsPanelProps) {
  const [agentResponse, setAgentResponse] = useState<string>('');
  const [loading, setLoading] = useState(false);
  const [hasLoaded, setHasLoaded] = useState(false);

  const fetchRecommendations = async () => {
    setLoading(true);
    try {
      const result = await AgentCoreService.recommendation(
        `Recommend the top 3 vehicles for customer ${customerId}. Include match scores, key selling points, and monthly payment estimates.`,
        customerId
      );
      if (result.error) {
        toast.error('Recommendation Agent Error', { description: result.result.substring(0, 100) });
        setAgentResponse(`Error: ${result.result}`);
      } else {
        setAgentResponse(result.result);
        setHasLoaded(true);
      }
    } catch (error) {
      console.error('Error invoking recommendation agent:', error);
      toast.error('Failed to get recommendations');
      setAgentResponse(`Error: ${error instanceof Error ? error.message : 'Unknown error'}`);
    } finally {
      setLoading(false);
    }
  };

  return (
    <Card className="h-full">
      <CardHeader className="border-b">
        <div className="flex items-center justify-between">
          <CardTitle className="text-lg flex items-center gap-2">
            <Target className="h-5 w-5 text-blue-500" />
            AI Recommendations
          </CardTitle>
          <div className="flex items-center gap-2">
            {hasLoaded && (
              <Badge variant="outline" className="bg-green-500/10 text-green-600 border-green-500/20">
                Live
              </Badge>
            )}
            <Button
              size="sm"
              variant="outline"
              onClick={fetchRecommendations}
              disabled={loading}
            >
              {loading ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <RefreshCw className="h-4 w-4" />
              )}
            </Button>
          </div>
        </div>
      </CardHeader>
      <CardContent className="space-y-6 pt-6">
        {/* Agent-Powered Recommendations */}
        {!hasLoaded && !loading && (
          <div className="text-center py-8 space-y-4">
            <Car className="h-12 w-12 mx-auto text-muted-foreground/50" />
            <div>
              <p className="text-sm text-muted-foreground">Click below to get AI-powered vehicle recommendations</p>
              <p className="text-xs text-muted-foreground mt-1">for customer {customerId}</p>
            </div>
            <Button onClick={fetchRecommendations} disabled={loading}>
              {loading ? (
                <>
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  Analyzing...
                </>
              ) : (
                <>
                  <Target className="mr-2 h-4 w-4" />
                  Get Recommendations
                </>
              )}
            </Button>
          </div>
        )}

        {loading && (
          <div className="text-center py-8 space-y-3">
            <Loader2 className="h-8 w-8 mx-auto animate-spin text-blue-500" />
            <p className="text-sm text-muted-foreground">Analyzing customer profile and matching vehicles...</p>
            <p className="text-xs text-muted-foreground">This may take 15-30 seconds</p>
          </div>
        )}

        {agentResponse && !loading && (
          <div className="space-y-4">
            <div className="flex items-center gap-2">
              <Car className="h-5 w-5 text-blue-500" />
              <h4 className="font-semibold">Vehicle Recommendations</h4>
              <Badge variant="secondary" className="text-xs">Powered by AgentCore</Badge>
            </div>
            <div className="bg-blue-500/5 border border-blue-500/20 rounded-lg p-4">
              <pre className="whitespace-pre-wrap text-sm leading-relaxed overflow-x-auto">
                {agentResponse}
              </pre>
            </div>
          </div>
        )}

        {/* Cross-Sell Opportunities */}
        <div className="space-y-3">
          <div className="flex items-center gap-2">
            <Shield className="h-5 w-5 text-green-500" />
            <h4 className="font-semibold">Cross-Sell Opportunities</h4>
            <span className="text-xs text-muted-foreground">High Probability</span>
          </div>

          <div className="space-y-2">
            {[
              { name: 'Extended Warranty', price: 1850, probability: 78, description: '7yr/100K miles' },
              { name: 'GAP Insurance', price: 695, probability: 65, description: 'Full coverage' },
              { name: 'Paint Protection', price: 1200, probability: 52, description: 'Ceramic coating' }
            ].map((item, idx) => (
              <div key={idx} className="flex items-center justify-between p-3 border rounded-lg hover:border-green-500/40 hover:shadow-sm transition-all duration-150">
                <div className="flex-1">
                  <div className="font-medium text-sm">{item.name}</div>
                  <div className="text-xs text-muted-foreground">{item.description} - ${item.price}</div>
                  <div className="text-xs text-green-600 mt-1">{item.probability}% acceptance probability</div>
                </div>
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => {
                    toast.success('Product Added', {
                      description: `${item.name} added to deal`,
                      duration: 2500,
                    });
                  }}
                >
                  Add
                </Button>
              </div>
            ))}
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
