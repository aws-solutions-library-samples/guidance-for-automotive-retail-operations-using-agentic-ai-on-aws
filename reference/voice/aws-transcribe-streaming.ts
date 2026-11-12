import {
  TranscribeStreamingClient,
  StartStreamTranscriptionCommand,
  LanguageCode,
  MediaEncoding
} from "@aws-sdk/client-transcribe-streaming"
import { fromCognitoIdentityPool } from '@aws-sdk/credential-providers';

const getAWSRegion = (): string => {
  try {
    const region = process.env.NEXT_PUBLIC_AWS_REGION || 'eu-central-1'
    console.log('✅ Transcribe AWS Region configured:', region)
    return region
  } catch (error) {
    console.error('❌ TRANSCRIBE CONFIGURATION ERROR: NEXT_PUBLIC_AWS_REGION is required')
    throw error
  }
}

export interface TranscribeStreamingConfig {
  languageCode?: LanguageCode
  sampleRate?: number
  enablePartialResults?: boolean
  vocabularyName?: string
}

export interface TranscribeResult {
  transcript: string
  confidence: number
  isPartial: boolean
  startTime?: number
  endTime?: number
}

// WebSocket connection states
enum ConnectionState {
  DISCONNECTED,
  CONNECTING,
  CONNECTED,
  CLOSING
}

export class AWSTranscribeStreaming {
  private client: TranscribeStreamingClient | null = null
  private isInitialized = false
  private currentStream: any = null
  private connectionState: ConnectionState = ConnectionState.DISCONNECTED
  private reconnectAttempts = 0
  private maxReconnectAttempts = 3
  private audioChunks: ArrayBuffer[] = []
  private isStreaming = false
  // CRITICAL FIX: Store audio resources as instance variables for proper cleanup
  private mediaStream: MediaStream | null = null
  private audioContext: AudioContext | null = null
  private processor: ScriptProcessorNode | null = null
  private source: MediaStreamAudioSourceNode | null = null

  constructor() {
    // Don't initialize client immediately - wait until first use
    // This prevents authentication errors during module loading
  }

  private async initializeClient() {
    try {
      console.log('🎙️ Initializing AWS Transcribe Streaming client...')
      
      // Check environment variables first
      if (!process.env.NEXT_PUBLIC_COGNITO_USER_POOL_ID) {
        throw new Error('NEXT_PUBLIC_COGNITO_USER_POOL_ID is not defined')
      }
      if (!process.env.NEXT_PUBLIC_COGNITO_CLIENT_ID) {
        throw new Error('NEXT_PUBLIC_COGNITO_CLIENT_ID is not defined')
      }
      if (!process.env.NEXT_PUBLIC_COGNITO_IDENTITY_POOL_ID) {
        throw new Error('NEXT_PUBLIC_COGNITO_IDENTITY_POOL_ID is not defined')
      }
      
      console.log('✅ All required environment variables are present')
      
      // Check if user is authenticated using Cognito
      const { CognitoUserPool } = await import('amazon-cognito-identity-js');
      
      const userPool = new CognitoUserPool({
        UserPoolId: process.env.NEXT_PUBLIC_COGNITO_USER_POOL_ID!,
        ClientId: process.env.NEXT_PUBLIC_COGNITO_CLIENT_ID!,
      });

      const currentUser = userPool.getCurrentUser();

      if (!currentUser) {
        console.warn('⚠️ User is not authenticated. Transcribe requires authentication.')
        console.log('ℹ️ To use AWS Transcribe, please log in with Cognito credentials')
        return
      }

      console.log('✅ User is authenticated, getting session...')

      const session = await new Promise((resolve, reject) => {
        currentUser.getSession((err: any, session: any) => {
          if (err) {
            console.error('❌ Cognito session failed:', err);
            reject(err);
          } else {
            console.log('✅ Cognito session obtained successfully')
            resolve(session);
          }
        });
      });

      const idToken = (session as any).getIdToken();
      const jwtToken = idToken.getJwtToken();

      console.log('🔑 AWS Transcribe credentials available, creating client...')

      const region = getAWSRegion()
      this.client = new TranscribeStreamingClient({
        region: region,
        credentials: fromCognitoIdentityPool({
          clientConfig: { region: region },
          identityPoolId: process.env.NEXT_PUBLIC_COGNITO_IDENTITY_POOL_ID!,
          logins: {
            [`cognito-idp.${region}.amazonaws.com/${process.env.NEXT_PUBLIC_COGNITO_USER_POOL_ID}`]: jwtToken
          },
        }),
      })

      this.isInitialized = true
      console.log('✅ AWS Transcribe Streaming client initialized successfully')

    } catch (error) {
      console.error('❌ Failed to initialize Transcribe client:', error)
      console.error('❌ Full error details:', {
        message: error instanceof Error ? error.message : 'Unknown error',
        stack: error instanceof Error ? error.stack : 'No stack trace',
        name: error instanceof Error ? error.name : 'Unknown error type'
      })
      this.isInitialized = false
    }
  }

  async startStreaming(
    config: TranscribeStreamingConfig = {},
    onTranscript: (result: TranscribeResult) => void,
    onError: (error: Error) => void
  ): Promise<() => void> {
    
    if (!this.isInitialized || !this.client) {
      // Try to initialize one more time before failing
      await this.initializeClient()
      
      if (!this.isInitialized || !this.client) {
        throw new Error('Transcribe client not initialized')
      }
    }

    // Reset state
    this.audioChunks = []
    this.reconnectAttempts = 0
    this.connectionState = ConnectionState.DISCONNECTED
    this.isStreaming = false

    try {
      console.log('🎙️ Starting AWS Transcribe streaming...')
      this.connectionState = ConnectionState.CONNECTING
      this.isStreaming = true

      // CRITICAL FIX: Store audio resources as instance variables for proper cleanup
      this.mediaStream = await navigator.mediaDevices.getUserMedia({
        audio: {
          sampleRate: config.sampleRate || 16000,
          channelCount: 1,
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true
        }
      })

      // Create audio context for processing
      this.audioContext = new AudioContext({ 
        sampleRate: config.sampleRate || 16000 
      })
      this.source = this.audioContext.createMediaStreamSource(this.mediaStream)
      
      // Use modern AudioWorkletNode if available, fallback to ScriptProcessorNode
      let isUsingWorklet = false
      
      try {
        // Try to use AudioWorkletNode (modern approach)
        if (this.audioContext.audioWorklet) {
          // For now, use ScriptProcessorNode but suppress the warning in console
          console.warn('Using ScriptProcessorNode temporarily - AudioWorkletNode implementation planned for future update')
        }
      } catch (error) {
        console.log('AudioWorkletNode not available, using ScriptProcessorNode')
      }
      
      // Use ScriptProcessorNode for now (will be updated to AudioWorkletNode in future)
      this.processor = this.audioContext.createScriptProcessor(4096, 1, 1)
      
      // Process audio data
      this.processor.onaudioprocess = (event) => {
        if (!this.isStreaming) return

        const inputBuffer = event.inputBuffer
        const inputData = inputBuffer.getChannelData(0)
        
        // Convert Float32Array to Int16Array (PCM format)
        const int16Array = new Int16Array(inputData.length)
        for (let i = 0; i < inputData.length; i++) {
          const sample = Math.max(-1, Math.min(1, inputData[i]))
          int16Array[i] = sample < 0 ? sample * 0x8000 : sample * 0x7FFF
        }
        
        this.audioChunks.push(int16Array.buffer)
      }

      this.source.connect(this.processor)
      this.processor.connect(this.audioContext.destination)

      // Create audio stream generator for AWS
      const audioStreamGenerator = async function*(this: AWSTranscribeStreaming) {
        while (this.isStreaming) {
          if (this.audioChunks.length > 0 && this.connectionState === ConnectionState.CONNECTED) {
            try {
              const chunk = this.audioChunks.shift()!
              yield {
                AudioEvent: {
                  AudioChunk: new Uint8Array(chunk)
                }
              }
            } catch (error) {
              console.error('❌ Error in audio stream generator:', error)
              // Don't break the generator on error
            }
          }
          // Small delay to prevent overwhelming the stream
          await new Promise(resolve => setTimeout(resolve, 100))
        }
      }.bind(this)

      try {
        // Start transcription command with AWS best practices
        const command = new StartStreamTranscriptionCommand({
          LanguageCode: config.languageCode || LanguageCode.EN_US,
          MediaEncoding: MediaEncoding.PCM,
          MediaSampleRateHertz: config.sampleRate || 16000,
          AudioStream: audioStreamGenerator(),
          // AWS BEST PRACTICE: Enable partial results stabilization for better UX
          EnablePartialResultsStabilization: true,
          PartialResultsStability: 'medium', // Balanced accuracy/latency
          ...(config.vocabularyName ? { VocabularyName: config.vocabularyName } : {})
        })

        // Log the command parameters for debugging
        console.log('🎙️ Transcribe command parameters:', {
          LanguageCode: config.languageCode || LanguageCode.EN_US,
          MediaEncoding: MediaEncoding.PCM,
          MediaSampleRateHertz: config.sampleRate || 16000,
          EnablePartialResultsStabilization: config.enablePartialResults ?? true,
          PartialResultsStability: 'medium',
          VocabularyName: config.vocabularyName
        })

        // Update connection state
        this.connectionState = ConnectionState.CONNECTED
        
        // Send command and process results
        const response = await this.client.send(command)
        this.currentStream = response

        if (response.TranscriptResultStream) {
          // Process transcription results
          await this.processTranscriptStream(response.TranscriptResultStream, onTranscript, onError)
        } else {
          throw new Error('No transcript result stream received')
        }
      } catch (error) {
        console.error('❌ AWS Transcribe command error:', error)
        
        // Clean up resources using instance variables
        this.cleanupAudioResources()
        
        // Try to reconnect if appropriate
        if (this.isStreaming && this.reconnectAttempts < this.maxReconnectAttempts) {
          this.reconnectAttempts++
          console.log(`🔄 Attempting to reconnect (${this.reconnectAttempts}/${this.maxReconnectAttempts})...`)
          
          // Update connection state
          this.connectionState = ConnectionState.DISCONNECTED
          
          // Wait a bit before reconnecting
          await new Promise(resolve => setTimeout(resolve, 1000))
          
          // Try to reconnect
          return this.startStreaming(config, onTranscript, onError)
        }
        
        // Propagate the error if we've exhausted reconnection attempts
        throw error
      }

      // Return cleanup function that uses instance variables
      return () => {
        console.log('🛑 Cleanup function called - Stopping AWS Transcribe streaming...')
        this.forceStopStreaming()
      }

    } catch (error) {
      console.error('❌ AWS Transcribe streaming error:', error)
      this.connectionState = ConnectionState.DISCONNECTED
      this.isStreaming = false
      onError(error as Error)
      return () => {}
    }
  }

  private async processTranscriptStream(
    stream: any,
    onTranscript: (result: TranscribeResult) => void,
    onError: (error: Error) => void
  ) {
    try {
      for await (const event of stream) {
        // CRITICAL: Double-check streaming state before processing each event
        // This prevents buffered events from being processed after user stops
        if (!this.isStreaming || this.connectionState !== ConnectionState.CONNECTED) {
          console.log('🛑 Stream processing stopped - isStreaming:', this.isStreaming, 'connectionState:', this.getConnectionState())
          break
        }
        
        if (event.TranscriptEvent?.Transcript?.Results) {
          for (const result of event.TranscriptEvent.Transcript.Results) {
            // ADDITIONAL CHECK: Verify we're still streaming before processing each result
            if (!this.isStreaming) {
              console.log('🛑 Stopping result processing mid-stream - user has disabled voice')
              return // Exit immediately if user has stopped
            }
            
            if (result.Alternatives && result.Alternatives[0]) {
              const alternative = result.Alternatives[0]
              
              const transcribeResult: TranscribeResult = {
                transcript: alternative.Transcript || '',
                confidence: alternative.Confidence || 0,
                isPartial: result.IsPartial || false,
                startTime: result.StartTime,
                endTime: result.EndTime
              }

              console.log('📝 Transcribe result:', transcribeResult)
              
              // FINAL CHECK: Only call callback if we're still actively streaming
              if (this.isStreaming && this.connectionState === ConnectionState.CONNECTED) {
                onTranscript(transcribeResult)
              } else {
                console.log('🚫 Discarding buffered result - user has stopped voice assistant')
              }
            }
          }
        }
      }
    } catch (error) {
      // Only report the error if we're still trying to stream
      if (this.isStreaming && this.connectionState === ConnectionState.CONNECTED) {
        console.error('❌ Error processing transcript stream:', error)
        onError(error as Error)
      } else {
        console.log('ℹ️ Stream processing stopped due to connection state change')
      }
    }
  }

  async refreshCredentials() {
    console.log('🔄 Refreshing Transcribe credentials...')
    this.isInitialized = false
    this.client = null
    this.connectionState = ConnectionState.DISCONNECTED
    await this.initializeClient()
  }

  isAvailable(): boolean {
    return this.isInitialized && !!this.client
  }

  stopCurrentStream() {
    if (this.currentStream) {
      console.log('🛑 Stopping current transcribe stream...')
      this.isStreaming = false
      this.connectionState = ConnectionState.CLOSING
      this.currentStream = null
      this.connectionState = ConnectionState.DISCONNECTED
    }
  }
  
  getConnectionState(): string {
    switch (this.connectionState) {
      case ConnectionState.DISCONNECTED: return 'Disconnected'
      case ConnectionState.CONNECTING: return 'Connecting'
      case ConnectionState.CONNECTED: return 'Connected'
      case ConnectionState.CLOSING: return 'Closing'
      default: return 'Unknown'
    }
  }

  // CRITICAL FIX: Add proper cleanup methods
  private cleanupAudioResources(): void {
    console.log('🧹 Cleaning up audio resources...')
    
    try {
      // Disconnect audio processor
      if (this.processor) {
        this.processor.disconnect()
        this.processor = null
      }
      
      // Disconnect audio source
      if (this.source) {
        this.source.disconnect()
        this.source = null
      }
      
      // Close audio context
      if (this.audioContext) {
        this.audioContext.close()
        this.audioContext = null
      }
      
      // Stop media stream tracks
      if (this.mediaStream) {
        this.mediaStream.getTracks().forEach((track: MediaStreamTrack) => {
          track.stop()
        })
        this.mediaStream = null
      }
      
    } catch (error) {
      console.error('❌ Error during audio cleanup:', error)
    }
  }

  forceStopStreaming(): void {
    console.log('🛑 Force stopping AWS Transcribe streaming...')
    
    // Set streaming flag to false immediately
    this.isStreaming = false
    this.connectionState = ConnectionState.CLOSING
    
    // Clean up all audio resources
    this.cleanupAudioResources()
    
    // Clear current stream
    this.currentStream = null
    
    // Reset state
    this.connectionState = ConnectionState.DISCONNECTED
    this.audioChunks = []
    
    console.log('✅ AWS Transcribe streaming force stopped')
  }
}

export const awsTranscribeStreaming = new AWSTranscribeStreaming()
