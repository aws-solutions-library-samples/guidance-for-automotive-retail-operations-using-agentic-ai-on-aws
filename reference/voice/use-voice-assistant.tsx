"use client";

import { useState, useEffect, useRef, useCallback } from 'react';
import { TranscribeService } from '@/lib/transcribe-service';
import { useSettings } from '@/contexts/SettingsContext';

interface VoiceCommand {
  command: string;
  confidence: number;
  timestamp: number;
}

interface UseVoiceAssistantOptions {
  continuous?: boolean;
  interimResults?: boolean;
  language?: string;
  confidenceThreshold?: number;
}

export function useVoiceAssistant(options: UseVoiceAssistantOptions = {}) {
  const {
    continuous = false,
    interimResults = false,
    language = 'en-US',
    confidenceThreshold = 0.6
  } = options;

  const { settings } = useSettings();

  // Core states
  const [isListening, setIsListening] = useState(false);
  const [isSpeaking, setIsSpeaking] = useState(false);
  const [isProcessing, setIsProcessing] = useState(false);
  const [isThinking, setIsThinking] = useState(false);
  const [isSupported, setIsSupported] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lastCommand, setLastCommand] = useState<VoiceCommand | null>(null);
  const [audioLevel, setAudioLevel] = useState(0);
  const [usingTranscribe, setUsingTranscribe] = useState(false);

  // Refs for cleanup and state management
  const recognition = useRef<any>(null);
  const transcribeService = useRef<TranscribeService | null>(null);
  const isInitialized = useRef(false);
  const globalLock = useRef(false); // Prevent overlapping operations
  const silenceTimer = useRef<NodeJS.Timeout | null>(null); // Track silence for auto-submit

  // Initialize speech recognition
  useEffect(() => {
    if (typeof window !== 'undefined' && !isInitialized.current) {
      const SpeechRecognition = (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;
      
      if (SpeechRecognition) {
        recognition.current = new SpeechRecognition();
        recognition.current.continuous = continuous;
        recognition.current.interimResults = interimResults;
        recognition.current.lang = language;
        recognition.current.maxAlternatives = 1;

        console.log('✅ useVoiceAssistant: Browser speech recognition initialized');
      } else {
        console.log('ℹ️ useVoiceAssistant: Browser speech recognition not available, will use AWS Transcribe');
      }
      
      // ALWAYS set supported to true since we have AWS Transcribe as fallback
      setIsSupported(true);
      console.log('✅ useVoiceAssistant: Voice assistant supported (Browser + AWS Transcribe)');
      
      isInitialized.current = true;
    }
  }, [continuous, interimResults, language]);

  const startListening = useCallback(async (): Promise<void> => {
    if (isListening) {
      console.warn('⚠️ Already listening - ignoring start request');
      return;
    }
    
    if (globalLock.current) {
      console.warn('⚠️ Operation in progress - ignoring start request');
      return;
    }

    // Set global lock to prevent overlapping operations
    globalLock.current = true;
    setError(null);

    try {
      // Try AWS Transcribe first if available and enabled in settings
      const transcribeEnabled = settings?.voice?.transcribeEnabled !== false; // Default to true if not set
      
      if (transcribeEnabled) {
        try {
          console.log('🎤 Attempting AWS Transcribe streaming...');
          await startTranscribeListening();
          return; // Success with AWS Transcribe
        } catch (error) {
          console.warn('⚠️ AWS Transcribe failed, falling back to browser speech recognition:', error);
          // Continue to browser fallback
        }
      } else {
        console.log('🎤 AWS Transcribe disabled in settings, using browser speech recognition');
      }

      // Fallback to browser speech recognition
      console.log('🎤 Using browser speech recognition...');
      await startBrowserListening();
      
    } catch (error) {
      console.error('❌ Failed to start listening:', error);
      globalLock.current = false;
      throw error;
    }
  }, [isListening, settings?.voice?.transcribeEnabled]);

  const startTranscribeListening = useCallback(async (): Promise<void> => {
    try {
      // Initialize Transcribe service if needed
      if (!transcribeService.current) {
        transcribeService.current = new TranscribeService(
          {
            silenceThresholdMs: 3000, // Consistent 3-second silence
          },
          {
            onResult: (result) => {
              console.log(`🎯 Transcribe result: "${result.transcript}" (confidence: ${result.confidence}, final: ${result.isFinal})`);
              
              if (result.isFinal && result.transcript.trim()) {
                setLastCommand({
                  command: result.transcript,
                  confidence: result.confidence,
                  timestamp: Date.now()
                });

                // Stop listening after final result for non-continuous mode
                if (!continuous) {
                  setTimeout(() => stopListening(), 100);
                }
              }
            },
            onError: (error) => {
              console.error('❌ Transcribe error:', error);
              setError(error);
              setIsListening(false);
              globalLock.current = false;
            },
            onSilenceDetected: () => {
              console.log('🔇 3-second silence detected by Transcribe');
              // Transcribe will handle silence and provide final result
            },
            onAudioLevel: (level) => {
              setAudioLevel(level);
            }
          }
        );
      }

      await transcribeService.current.startStreaming();
      setUsingTranscribe(true);
      setIsListening(true);
      globalLock.current = false;
      
    } catch (error: any) {
      console.error('❌ Transcribe streaming failed:', error);
      globalLock.current = false;
      throw error;
    }
  }, [continuous]);

  const startBrowserListening = useCallback(async (): Promise<void> => {
    if (!isSupported || !recognition.current) {
      setError('Speech recognition not supported in this browser');
      globalLock.current = false;
      return;
    }

    try {
      console.log('🎤 Starting browser speech recognition (fallback)...');

      // Clear previous handlers
      recognition.current.onstart = null;
      recognition.current.onend = null;
      recognition.current.onerror = null;
      recognition.current.onresult = null;

      // Set up event handlers
      recognition.current.onstart = () => {
        console.log('✅ Browser speech recognition started');
        setIsListening(true);
        setUsingTranscribe(false);
        globalLock.current = false; // Release lock once started
      };

      recognition.current.onend = () => {
        console.log('🛑 Browser speech recognition ended');
        setIsListening(false);
        globalLock.current = false; // Release lock when ended
      };

      recognition.current.onerror = (event: any) => {
        // Handle specific errors gracefully - different logging for different error types
        if (event.error === 'no-speech') {
          console.log('✅ No speech detected - normal behavior, restarting listener');
          // Don't set error for no-speech, it's normal behavior - just end listening
          setIsListening(false);
          globalLock.current = false;
          return; // Don't set error state for no-speech
        } else if (event.error === 'audio-capture') {
          console.error('❌ Audio capture error - microphone issue');
          setError('Microphone access issue. Please check your microphone.');
        } else if (event.error === 'not-allowed') {
          console.error('❌ Permission denied - user blocked microphone access'); 
          setError('Microphone permission denied. Please allow microphone access.');
        } else {
          console.error('❌ Unexpected browser speech recognition error:', event.error);
          setError(event.error);
        }
        
        setIsListening(false);
        globalLock.current = false; // Release lock on error
      };

      recognition.current.onresult = (event: any) => {
        console.log('📝 Browser speech recognition result received');
        
        for (let i = event.resultIndex; i < event.results.length; i++) {
          const result = event.results[i];
          const transcript = result[0].transcript.trim();
          const confidence = result[0].confidence || 0.8;

          if (result.isFinal && transcript) {
            console.log(`🎯 Final browser transcript: "${transcript}" (confidence: ${confidence})`);
            
            setLastCommand({
              command: transcript,
              confidence: confidence,
              timestamp: Date.now()
            });

            // Stop listening after final result for non-continuous mode
            if (!continuous) {
              setTimeout(() => stopListening(), 100);
            }
          } else if (interimResults && transcript) {
            console.log(`🎯 Interim browser transcript: "${transcript}"`);
          }
        }
      };

      recognition.current.start();
      
    } catch (error: any) {
      console.error('❌ Failed to start browser listening:', error);
      setError(error.message);
      setIsListening(false);
      globalLock.current = false;
    }
  }, [isSupported, continuous, interimResults]);

  const stopListening = useCallback(async (): Promise<void> => {
    if (!isListening) {
      return;
    }

    try {
      console.log('🛑 useVoiceAssistant: Stopping speech recognition...');
      
      // Stop AWS Transcribe if using it
      if (usingTranscribe && transcribeService.current) {
        await transcribeService.current.stopStreaming();
        setUsingTranscribe(false);
      }
      
      // Stop browser recognition if using it
      if (!usingTranscribe && recognition.current) {
        recognition.current.stop();
      }
      
      setIsListening(false);
      globalLock.current = false;
      
    } catch (error: any) {
      console.error('❌ useVoiceAssistant: Error stopping recognition:', error);
      setError(error.message);
      setIsListening(false);
      globalLock.current = false;
    }
  }, [isListening, usingTranscribe]);

  const speak = useCallback(async (text: string, options: { rate?: number; pitch?: number } = {}): Promise<void> => {
    if (!text.trim()) return;

    // Allow speech during active listening or when not listening
    // Only block if we're actively starting/stopping (which should be brief)
    if (globalLock.current) {
      console.log('🔄 Speech delayed - waiting for operation to complete...');
      // Wait a brief moment for any ongoing operation to complete
      await new Promise(resolve => setTimeout(resolve, 100));
      
      // If still locked after brief wait, proceed anyway (don't block speech indefinitely)
      if (globalLock.current) {
        console.log('⚠️ Proceeding with speech despite active operation');
      }
    }
    
    setIsSpeaking(true);

    try {
      // Use browser TTS for now - can be enhanced with Polly later
      await speakWithBrowser(text, options);
      console.log('✅ useVoiceAssistant: Speech completed');
    } catch (error: any) {
      console.error('❌ useVoiceAssistant: Speech failed:', error);
      setError(error.message);
    } finally {
      setIsSpeaking(false);
    }
  }, []);

  const speakWithBrowser = useCallback((text: string, options: { rate?: number; pitch?: number } = {}): Promise<void> => {
    return new Promise((resolve, reject) => {
      if (!window.speechSynthesis) {
        reject(new Error('Speech synthesis not supported'));
        return;
      }

      const utterance = new SpeechSynthesisUtterance(text);
      utterance.rate = options.rate || 0.9;
      utterance.pitch = options.pitch || 1.0;
      utterance.volume = 0.8;
      utterance.lang = language;
      
      utterance.onend = () => {
        console.log('✅ Browser TTS completed');
        resolve();
      };

      utterance.onerror = (event: any) => {
        console.error('❌ Browser TTS error:', event);
        reject(new Error(`Speech synthesis error: ${event.error}`));
      };

      window.speechSynthesis.speak(utterance);
    });
  }, [language]);

  const stopSpeaking = useCallback((): void => {
    console.log('🛑 useVoiceAssistant: Stopping speech...');
    
    try {
      // Stop browser TTS
      if (window.speechSynthesis && window.speechSynthesis.speaking) {
        window.speechSynthesis.cancel();
      }
    } catch (error: any) {
      console.error('❌ useVoiceAssistant: Error stopping speech:', error);
    }
    
    setIsSpeaking(false);
    globalLock.current = false;
  }, []);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      console.log('🧹 useVoiceAssistant: Cleanup on unmount');
      
      // Stop Transcribe service if active
      if (transcribeService.current && usingTranscribe) {
        transcribeService.current.stopStreaming();
      }
      
      // Stop browser recognition if active
      if (recognition.current && isListening && !usingTranscribe) {
        recognition.current.stop();
      }
      
      stopSpeaking();
    };
  }, [isListening, usingTranscribe]);

  return {
    // States
    isListening,
    isSpeaking,
    isProcessing,
    isThinking,
    isSupported,
    error,
    lastCommand,
    audioLevel,
    usingTranscribe,

    // Actions
    startListening,
    stopListening,
    speak,
    stopSpeaking,

    // State setters for external control
    setProcessing: setIsProcessing,
    setThinking: setIsThinking,
    setError: (error: string | null) => setError(error)
  };
}
