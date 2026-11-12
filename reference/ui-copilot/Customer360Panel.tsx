"use client";

import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Mail, Phone, MapPin, Clock, User, Bot } from 'lucide-react';
import { Customer } from '@/types/customer';

interface Customer360PanelProps {
  customer: Customer;
}

export function Customer360Panel({ customer }: Customer360PanelProps) {
  return (
    <Card className="h-full">
      <CardHeader className="border-b">
        <div className="flex items-center justify-between">
          <CardTitle className="text-lg flex items-center gap-2">
            <User className="h-5 w-5 text-blue-500" />
            Customer 360 View
          </CardTitle>
          <Badge variant="outline" className="bg-green-500/10 text-green-600 border-green-500/20">
            Live Data
          </Badge>
        </div>
      </CardHeader>
      <CardContent className="space-y-6 pt-6">
        {/* Profile Main */}
        <div className="flex items-start gap-4">
          <div className="h-16 w-16 rounded-full bg-gradient-to-br from-blue-500 to-purple-500 flex items-center justify-center text-white text-xl font-bold">
            {customer.firstName[0]}{customer.lastName[0]}
          </div>
          <div className="flex-1">
            <h3 className="text-xl font-bold">{customer.firstName} {customer.lastName}</h3>
            <div className="flex items-center gap-2 mt-1">
              <span className="text-sm text-muted-foreground">Lead Score:</span>
              <span className={`font-bold ${customer.leadScore >= 90 ? 'text-green-600' : 'text-orange-600'}`}>
                {customer.leadScore}
              </span>
              <span className="text-sm text-muted-foreground">|</span>
              <span className="text-sm text-muted-foreground">
                Budget: ${(customer.budget.min / 1000).toFixed(0)}K-${(customer.budget.max / 1000).toFixed(0)}K
              </span>
            </div>
          </div>
        </div>

        {/* Contact Information */}
        <div>
          <h4 className="text-sm font-semibold mb-3">Contact Information</h4>
          <div className="space-y-2">
            <div className="flex items-center gap-2 text-sm">
              <Mail className="h-4 w-4 text-muted-foreground" />
              <span>{customer.email}</span>
            </div>
            <div className="flex items-center gap-2 text-sm">
              <Phone className="h-4 w-4 text-muted-foreground" />
              <span>{customer.phone}</span>
            </div>
            <div className="flex items-center gap-2 text-sm">
              <MapPin className="h-4 w-4 text-muted-foreground" />
              <span>{customer.location}</span>
            </div>
            <div className="flex items-center gap-2 text-sm">
              <Clock className="h-4 w-4 text-muted-foreground" />
              <span>Preferred: {customer.preferredContact} (9AM-5PM)</span>
            </div>
          </div>
        </div>

        {/* Recent Activity */}
        <div>
          <h4 className="text-sm font-semibold mb-3">Recent Activity</h4>
          <div className="space-y-3">
            {customer.recentActivity.map((activity, index) => (
              <div key={index} className="flex gap-3">
                <div className="text-xs text-muted-foreground whitespace-nowrap">
                  {new Date(activity.timestamp).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}
                </div>
                <div className="text-sm flex-1">{activity.description}</div>
              </div>
            ))}
          </div>
        </div>

        {/* Preferences & Insights */}
        <div>
          <h4 className="text-sm font-semibold mb-3">Preferences & Insights</h4>
          <div className="flex flex-wrap gap-2">
            {customer.preferences.priorities.map((pref, index) => (
              <Badge key={index} variant="secondary" className="text-xs">
                {pref}
              </Badge>
            ))}
          </div>
        </div>

        {/* AI Insight */}
        <div className="bg-blue-500/10 border border-blue-500/20 rounded-lg p-4">
          <div className="flex items-start gap-3">
            <Bot className="h-6 w-6 text-blue-500 flex-shrink-0 mt-0.5" />
            <div className="flex-1">
              <div className="font-semibold text-sm mb-1">AI Insight</div>
              <p className="text-sm text-muted-foreground">
                {customer.insights.join('. ')}
              </p>
            </div>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
