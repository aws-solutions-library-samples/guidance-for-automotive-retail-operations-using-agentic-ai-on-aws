"use client";

import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';
import { Target, Swords, BarChart3, Gift, Zap, CheckCircle, Circle, Loader2, Send } from 'lucide-react';
import { getCompetitiveIntel, getDealMetrics } from '@/lib/sales-data';
import { useEffect, useState } from 'react';
import { AgentCoreService } from '@/lib/agentcore-service';
import { toast } from 'sonner';

interface LiveAssistancePanelProps {
  customerId: string;
}

export function LiveAssistancePanel({ customerId }: LiveAssistancePanelProps) {
  const [objectionText, setObjectionText] = useState('');
  const [objectionResponse, setObjectionResponse] = useState<string>('');
  const [loading, setLoading] = useState(false);
  const [competitorIntel, setCompetitorIntel] = useState<any>(null);
  const [metrics, setMetrics] = useState<any>(null);

  useEffect(() => {
    const intel = getCompetitiveIntel('RAV4');
    const dealMetrics = getDealMetrics(customerId);

    setCompetitorIntel(intel);
    setMetrics(dealMetrics);
  }, [customerId]);

  const handleObjectionSubmit = async () => {
    if (!objectionText.trim()) {
      toast.error('Please enter an objection');
      return;
    }

    setLoading(true);
    setObjectionResponse('');

    try {
      const result = await AgentCoreService.objectionHandler(objectionText, customerId);

      if (result.error) {
        toast.error('Agent Error', { description: result.result });
        setObjectionResponse(`Error: ${result.result}`);
      } else {
        setObjectionResponse(result.result);
        toast.success('Objection Handler Response', {
          description: 'AI-powered strategy provided',
        });
      }
    } catch (error) {
      console.error('Error invoking objection handler:', error);
      toast.error('Failed to get objection response');
      setObjectionResponse(`Error: ${error instanceof Error ? error.message : 'Unknown error'}`);
    } finally {
      setLoading(false);
    }
  };

  const useExample = (example: string) => {
    setObjectionText(example);
  };

  return (
    <Card className="h-full">
      <CardHeader className="border-b">
        <div className="flex items-center justify-between">
          <CardTitle className="text-lg flex items-center gap-2">
            <Zap className="h-5 w-5 text-amber-500" />
            Live Assistance
          </CardTitle>
          <Badge variant="outline" className="bg-green-500/10 text-green-600 border-green-500/20">
            Active
          </Badge>
        </div>
      </CardHeader>
      <CardContent className="space-y-6 pt-6">
        {/* AI Objection Handler */}
        <div className="space-y-3">
          <div className="flex items-center gap-2">
            <Target className="h-5 w-5 text-orange-500" />
            <h4 className="font-semibold text-sm">AI Objection Handler</h4>
            <Badge variant="secondary" className="text-xs">
              Powered by AgentCore
            </Badge>
          </div>

          <div className="space-y-3">
            <Textarea
              placeholder="Enter customer objection... (e.g., 'The price is too high')"
              value={objectionText}
              onChange={(e) => setObjectionText(e.target.value)}
              rows={2}
              className="resize-none"
            />

            <div className="flex gap-2">
              <Button
                size="sm"
                onClick={handleObjectionSubmit}
                disabled={loading || !objectionText.trim()}
                className="flex-1"
              >
                {loading ? (
                  <>
                    <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                    Analyzing...
                  </>
                ) : (
                  <>
                    <Send className="mr-2 h-4 w-4" />
                    Get AI Response
                  </>
                )}
              </Button>
            </div>

            {/* Quick Examples */}
            <div className="flex flex-wrap gap-2">
              {['Price is too high', 'Missing features', 'Need to think about it', 'Comparing to Toyota'].map((example) => (
                <Button
                  key={example}
                  size="sm"
                  variant="outline"
                  onClick={() => useExample(example)}
                  className="text-xs"
                >
                  {example}
                </Button>
              ))}
            </div>

            {/* AI Response */}
            {objectionResponse && (
              <div className="bg-blue-500/10 border border-blue-500/20 rounded-lg p-4 space-y-3">
                <div className="flex items-center justify-between">
                  <Badge variant="secondary" className="text-xs">
                    AI Objection Strategy
                  </Badge>
                </div>
                <div className="prose prose-sm max-w-none">
                  <pre className="whitespace-pre-wrap text-xs bg-background/50 p-3 rounded overflow-x-auto">
                    {objectionResponse}
                  </pre>
                </div>
              </div>
            )}
          </div>
        </div>

        {/* Competitive Intelligence */}
        {competitorIntel && (
          <div className="space-y-3">
            <div className="flex items-center gap-2">
              <Swords className="h-5 w-5 text-red-500" />
              <h4 className="font-semibold text-sm">Competitive Intelligence</h4>
            </div>

            <div className="border rounded-lg p-4 space-y-3">
              <div className="flex items-center justify-between">
                <span className="font-medium text-sm">vs {competitorIntel.competitor}</span>
                <Badge variant="secondary">Win Rate: {competitorIntel.winRate}%</Badge>
              </div>
              <div>
                <div className="text-xs font-semibold mb-2">Your Advantages:</div>
                <ul className="space-y-1">
                  {competitorIntel.advantages.map((adv: string, idx: number) => (
                    <li key={idx} className="text-sm flex items-start gap-2">
                      <CheckCircle className="h-4 w-4 text-green-500 flex-shrink-0 mt-0.5" />
                      <span>{adv}</span>
                    </li>
                  ))}
                </ul>
              </div>
              <Button size="sm" variant="outline" className="w-full">
                View Full Comparison
              </Button>
            </div>
          </div>
        )}

        {/* Quick Stats */}
        {metrics && (
          <div className="space-y-3">
            <div className="flex items-center gap-2">
              <BarChart3 className="h-5 w-5 text-blue-500" />
              <h4 className="font-semibold text-sm">Quick Stats</h4>
            </div>

            <div className="grid grid-cols-2 gap-3">
              <div className="border rounded-lg p-3">
                <div className="text-xs text-muted-foreground">Avg Close Time</div>
                <div className="text-lg font-bold">{metrics.estimatedCloseTime}</div>
              </div>
              <div className="border rounded-lg p-3">
                <div className="text-xs text-muted-foreground">Similar Customers</div>
                <div className="text-lg font-bold">{metrics.similarCustomerConversion}% chose CR-V</div>
              </div>
              <div className="border rounded-lg p-3 col-span-2">
                <div className="text-xs text-muted-foreground">Inventory</div>
                <div className="text-lg font-bold">3 units available</div>
              </div>
            </div>
          </div>
        )}

        {/* Available Incentives */}
        <div className="space-y-3">
          <div className="flex items-center gap-2">
            <Gift className="h-5 w-5 text-purple-500" />
            <h4 className="font-semibold text-sm">Available Incentives</h4>
          </div>

          <div className="space-y-2">
            {[
              { name: 'College Graduate Program', amount: '$500 bonus cash', eligible: true },
              { name: 'Loyalty Bonus', amount: '$750 for current Honda owners', eligible: true },
              { name: 'Military Discount', amount: '$1,000', eligible: false }
            ].map((incentive, idx) => (
              <div key={idx} className={`flex items-start gap-3 p-3 border rounded-lg ${incentive.eligible ? 'border-green-500/20 bg-green-500/5' : 'opacity-50'}`}>
                <div>
                  {incentive.eligible ? (
                    <CheckCircle className="h-5 w-5 text-green-500" />
                  ) : (
                    <Circle className="h-5 w-5 text-muted-foreground" />
                  )}
                </div>
                <div className="flex-1">
                  <div className="text-sm font-medium">{incentive.name}</div>
                  <div className="text-xs text-muted-foreground">{incentive.amount}</div>
                </div>
              </div>
            ))}
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
