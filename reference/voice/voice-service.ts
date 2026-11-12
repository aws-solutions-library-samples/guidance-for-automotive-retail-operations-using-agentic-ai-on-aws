"use client"

import { 
  PollyClient, 
  SynthesizeSpeechCommand,
  VoiceId,
  OutputFormat,
  Engine
} from "@aws-sdk/client-polly"
import { fromCognitoIdentityPool } from '@aws-sdk/credential-providers';
import { awsTranscribeStreaming, TranscribeResult } from "@/lib/aws-transcribe-streaming"

export interface VoiceConfig {
  region: string
  identityPoolId: string
  voiceId?: VoiceId
}

export class VoiceService {
  private pollyClient: PollyClient | null = null
  private config: VoiceConfig
  private speechSynthesis: SpeechSynthesis | null = null
  private recognition: any = null
  private isRecording = false
  private isSpeaking = false
  private currentAudio: HTMLAudioElement | null = null
  private currentUtterance: SpeechSynthesisUtterance | null = null
  private transcribeStopFunction: (() => void) | null = null
  private transcriptTimeout: NodeJS.Timeout | null = null

  constructor(config: VoiceConfig) {
    this.config = {
      voiceId: 'Joanna' as VoiceId,
      ...config
    }
    
    this.initializeAWSClients()
    this.initializeBrowserAPIs()
  }

  private async initializeAWSClients() {
    try {
      // Use Cognito authentication similar to AWS Transcribe
      const { CognitoUserPool } = await import('amazon-cognito-identity-js');
      
      const userPool = new CognitoUserPool({
        UserPoolId: process.env.NEXT_PUBLIC_COGNITO_USER_POOL_ID!,
        ClientId: process.env.NEXT_PUBLIC_COGNITO_CLIENT_ID!,
      });

      const currentUser = userPool.getCurrentUser();

      if (currentUser) {
        const session = await new Promise((resolve, reject) => {
          currentUser.getSession((err: any, session: any) => {
            if (err) reject(err);
            else resolve(session);
          });
        });

        const idToken = (session as any).getIdToken();
        const jwtToken = idToken.getJwtToken();

        this.pollyClient = new PollyClient({
          region: this.config.region,
          credentials: fromCognitoIdentityPool({
            clientConfig: { region: this.config.region },
            identityPoolId: this.config.identityPoolId,
            logins: {
              [`cognito-idp.${this.config.region}.amazonaws.com/${process.env.NEXT_PUBLIC_COGNITO_USER_POOL_ID}`]: jwtToken
            },
          }),
        })
        console.log("✅ VoiceService: AWS Polly client initialized with authenticated credentials")
      } else {
        console.warn("⚠️ VoiceService: No authenticated user, using browser TTS only")
      }
    } catch (error) {
      console.warn("⚠️ VoiceService: Failed to initialize AWS Polly client, using browser TTS only:", error)
    }
  }

  private initializeBrowserAPIs() {
    if (typeof window !== 'undefined') {
      this.speechSynthesis = window.speechSynthesis
      
      const SpeechRecognition = (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition
      if (SpeechRecognition) {
        this.recognition = new SpeechRecognition()
        this.recognition.continuous = false
        this.recognition.interimResults = false
        this.recognition.lang = 'en-US'
        this.recognition.maxAlternatives = 1
      }
    }
  }

  // Speech to Text Methods (AWS Transcribe + Browser fallback)
  async startRecording(onTranscribeResult?: (result: any) => void): Promise<void> {
    if (this.isRecording) {
      console.warn("Already recording")
      return
    }

    try {
      this.isRecording = true
      console.log("Starting speech recognition...")
      
      // Check if user is authenticated using Cognito
      const { CognitoUserPool } = await import('amazon-cognito-identity-js');
      
      const userPool = new CognitoUserPool({
        UserPoolId: process.env.NEXT_PUBLIC_COGNITO_USER_POOL_ID!,
        ClientId: process.env.NEXT_PUBLIC_COGNITO_CLIENT_ID!,
      });

      const currentUser = userPool.getCurrentUser();
      
      if (currentUser) {
        console.log("🎙️ User is authenticated, trying AWS Transcribe Streaming")
        try {
          await this.startAWSTranscribeRecognition(onTranscribeResult)
          return // Success with AWS Transcribe
        } catch (error) {
          console.warn("⚠️ AWS Transcribe failed, falling back to browser:", error)
        }
      }
      
      // Fallback to browser speech recognition
      if (this.recognition) {
        console.log("📱 Using browser speech recognition")
        await this.startBrowserRecognition()
      } else {
        throw new Error("No speech recognition available")
      }
    } catch (error) {
      this.isRecording = false
      console.error("Failed to start recording:", error)
      throw error
    }
  }

  async stopRecording(): Promise<string> {
    if (!this.isRecording) {
      console.warn("Not recording")
      return ""
    }

    try {
      console.log("Stopping speech recognition...")
      let result = ""
      
      // Stop AWS Transcribe if active
      if (this.transcribeStopFunction) {
        result = await this.stopAWSTranscribeRecognition()
      } else if (this.recognition) {
        result = await this.stopBrowserRecognition()
      }
      
      this.isRecording = false
      return result
    } catch (error) {
      this.isRecording = false
      console.error("Failed to stop recording:", error)
      return ""
    }
  }

  // AWS Transcribe Methods
  private async startAWSTranscribeRecognition(onTranscribeResult?: (result: any) => void): Promise<void> {
    return new Promise((resolve, reject) => {
      let lastTranscript = ""
      
      const onTranscript = (result: TranscribeResult) => {
        console.log("📝 AWS Transcribe result:", result)
        
        // Forward result to React hook for proper state management
        if (onTranscribeResult) {
          onTranscribeResult(result)
        }
        
        // Keep track of last transcript for fallback
        if (!result.isPartial && result.transcript.trim()) {
          this.finalizeTranscript = result.transcript.trim()
        }
      }
      
      const onError = (error: Error) => {
        console.error("❌ AWS Transcribe error:", error)
        this.isRecording = false
        this.transcribeStopFunction = null
        reject(error)
      }
      
      // Start AWS Transcribe streaming
      awsTranscribeStreaming.startStreaming(
        {
          languageCode: 'en-US' as any,
          sampleRate: 16000,
          enablePartialResults: true
        },
        onTranscript,
        onError
      ).then((stopFunction) => {
        this.transcribeStopFunction = stopFunction
        console.log("✅ AWS Transcribe streaming started")
        resolve()
      }).catch((error) => {
        console.error("❌ Failed to start AWS Transcribe streaming:", error)
        this.isRecording = false
        reject(error)
      })
    })
  }
  
  private finalizeTranscript = ""
  
  private async stopAWSTranscribeRecognition(): Promise<string> {
    return new Promise((resolve) => {
      if (this.transcribeStopFunction) {
        console.log("🛑 Stopping AWS Transcribe streaming")
        this.transcribeStopFunction()
        this.transcribeStopFunction = null
        
        // Clear any pending transcript timeout
        if (this.transcriptTimeout) {
          clearTimeout(this.transcriptTimeout)
          this.transcriptTimeout = null
        }

        // Return the last finalized transcript
        const result = this.finalizeTranscript || ""
        this.finalizeTranscript = ""

        console.log("📝 Final AWS Transcribe result:", result)
        resolve(result)
      } else {
        console.warn("⚠️ No AWS Transcribe stream to stop")
        resolve("")
      }
    })
  }

  private async startBrowserRecognition(): Promise<void> {
    if (!this.recognition) {
      throw new Error("Speech recognition not supported")
    }

    return new Promise((resolve, reject) => {
      const timeout = setTimeout(() => {
        this.isRecording = false
        reject(new Error("Speech recognition start timeout"))
      }, 5000) // 5 second timeout for start

      // Clear any existing event handlers
      this.recognition.onstart = null
      this.recognition.onerror = null
      this.recognition.onend = null
      this.recognition.onresult = null

      this.recognition.onstart = () => {
        clearTimeout(timeout)
        console.log("Speech recognition started successfully")
        resolve()
      }
      
      this.recognition.onerror = (event: any) => {
        clearTimeout(timeout)
        this.isRecording = false
        console.error("Speech recognition start error:", event.error)
        reject(new Error(`Speech recognition error: ${event.error}`))
      }

      this.recognition.onend = () => {
        console.log("Speech recognition ended unexpectedly during start")
        this.isRecording = false
      }

      try {
        console.log("Calling recognition.start()...")
        this.recognition.start()
      } catch (error) {
        clearTimeout(timeout)
        this.isRecording = false
        console.error("Error calling recognition.start():", error)
        reject(error)
      }
    })
  }

  private async stopBrowserRecognition(): Promise<string> {
    if (!this.recognition) {
      throw new Error("Speech recognition not supported")
    }

    return new Promise((resolve, reject) => {
      let resolved = false
      const timeout = setTimeout(() => {
        if (!resolved) {
          resolved = true
          console.warn("Speech recognition result timeout")
          resolve("")
        }
      }, 5000) // 5 second timeout for results

      // Set up result handler
      this.recognition.onresult = (event: any) => {
        if (resolved) return
        resolved = true
        clearTimeout(timeout)
        
        console.log("Speech recognition result received:", event)
        
        try {
          if (event.results && event.results.length > 0) {
            const result = event.results[event.results.length - 1]
            if (result && result[0] && result[0].transcript) {
              const transcript = result[0].transcript.trim()
              console.log("Final transcript:", transcript)
              resolve(transcript)
            } else {
              console.log("No transcript in result")
              resolve("")
            }
          } else {
            console.log("No results in event")
            resolve("")
          }
        } catch (error) {
          console.error("Error processing speech result:", error)
          resolve("")
        }
      }
      
      this.recognition.onerror = (event: any) => {
        if (resolved) return
        resolved = true
        clearTimeout(timeout)
        console.error("Speech recognition error during stop:", event.error)
        
        // Don't reject on certain errors, just return empty string
        if (event.error === 'no-speech' || event.error === 'audio-capture') {
          resolve("")
        } else {
          reject(new Error(`Speech recognition error: ${event.error}`))
        }
      }

      this.recognition.onend = () => {
        console.log("Speech recognition session ended")
        if (!resolved) {
          resolved = true
          clearTimeout(timeout)
          resolve("")
        }
      }

      try {
        console.log("Calling recognition.stop()...")
        this.recognition.stop()
      } catch (error) {
        if (!resolved) {
          resolved = true
          clearTimeout(timeout)
          console.error("Error calling recognition.stop():", error)
          reject(error)
        }
      }
    })
  }

  // Text to Speech Methods
  async speak(text: string): Promise<void> {
    if (this.isSpeaking) {
      console.warn("Already speaking, stopping current speech")
      this.stopSpeaking()
      await new Promise(resolve => setTimeout(resolve, 100)) // Brief pause
    }

    if (!text || text.trim().length === 0) {
      console.warn("No text to speak")
      return
    }

    this.isSpeaking = true

    try {
      // Always refresh AWS credentials before speaking to ensure they're current
      console.log("🔄 VoiceService: Refreshing AWS credentials before TTS...")
      await this.initializeAWSClients()

      // Try AWS Polly first if available
      if (this.pollyClient) {
        console.log("Using AWS Polly for TTS")
        await this.speakWithPolly(text)
      } else {
        console.log("Using browser TTS")
        await this.speakWithBrowser(text)
      }
    } catch (error) {
      console.error("AWS Polly failed, falling back to browser TTS:", error)
      // Fallback to browser API
      try {
        await this.speakWithBrowser(text)
      } catch (browserError) {
        console.error("Browser TTS also failed:", browserError)
        throw browserError
      }
    } finally {
      this.isSpeaking = false
    }
  }

  private async speakWithPolly(text: string): Promise<void> {
    if (!this.pollyClient) throw new Error("Polly client not initialized")

    // Stop any current audio
    this.stopCurrentAudio()

    const command = new SynthesizeSpeechCommand({
      Text: text,
      OutputFormat: OutputFormat.MP3,
      VoiceId: this.config.voiceId,
      Engine: Engine.NEURAL
    })

    const response = await this.pollyClient.send(command)
    
    if (response.AudioStream) {
      const audioData = await response.AudioStream.transformToByteArray()
      const audioBlob = new Blob([new Uint8Array(audioData)], { type: 'audio/mp3' })
      const audioUrl = URL.createObjectURL(audioBlob)
      
      this.currentAudio = new Audio(audioUrl)
      
      return new Promise((resolve, reject) => {
        if (!this.currentAudio) {
          reject(new Error("Failed to create audio element"))
          return
        }

        const cleanup = () => {
          if (this.currentAudio) {
            this.currentAudio.removeEventListener('ended', onEnded)
            this.currentAudio.removeEventListener('error', onError)
            this.currentAudio.pause()
            this.currentAudio.currentTime = 0
            this.currentAudio = null
          }
          URL.revokeObjectURL(audioUrl)
        }

        const onEnded = () => {
          console.log("Polly audio playback ended")
          cleanup()
          resolve()
        }

        const onError = (error: any) => {
          console.error("Polly audio playback error:", error)
          cleanup()
          reject(error)
        }

        this.currentAudio.addEventListener('ended', onEnded)
        this.currentAudio.addEventListener('error', onError)
        
        // Set audio properties to prevent looping
        this.currentAudio.loop = false
        this.currentAudio.preload = 'auto'
        
        this.currentAudio.play().catch(reject)
      })
    }
  }

  private async speakWithBrowser(text: string): Promise<void> {
    if (!this.speechSynthesis) {
      throw new Error("Speech synthesis not supported")
    }

    // Stop any current speech
    this.stopCurrentSpeech()

    return new Promise((resolve, reject) => {
      this.currentUtterance = new SpeechSynthesisUtterance(text)
      this.currentUtterance.rate = 0.9
      this.currentUtterance.pitch = 1
      this.currentUtterance.volume = 0.8
      this.currentUtterance.lang = 'en-US'
      
      const cleanup = () => {
        if (this.currentUtterance) {
          this.currentUtterance.removeEventListener('end', onEnd)
          this.currentUtterance.removeEventListener('error', onError)
          this.currentUtterance = null
        }
      }

      const onEnd = () => {
        console.log("Browser TTS ended")
        cleanup()
        resolve()
      }

      const onError = (error: any) => {
        console.error("Browser TTS error:", error)
        cleanup()
        reject(error)
      }

      this.currentUtterance.addEventListener('end', onEnd)
      this.currentUtterance.addEventListener('error', onError)
      
      this.speechSynthesis!.speak(this.currentUtterance)
    })
  }

  // Stop methods
  stopSpeaking(): void {
    console.log("Stopping all speech")
    this.stopCurrentAudio()
    this.stopCurrentSpeech()
    this.isSpeaking = false
  }

  private stopCurrentAudio(): void {
    if (this.currentAudio) {
      console.log("Stopping Polly audio")
      this.currentAudio.pause()
      this.currentAudio.currentTime = 0
      this.currentAudio = null
    }
  }

  private stopCurrentSpeech(): void {
    if (this.speechSynthesis && this.speechSynthesis.speaking) {
      console.log("Stopping browser TTS")
      this.speechSynthesis.cancel()
    }
    this.currentUtterance = null
  }

  stopRecordingImmediate(): void {
    if (this.recognition && this.isRecording) {
      console.log("Force stopping speech recognition")
      this.recognition.stop()
      this.isRecording = false
    }
  }

  // Status methods
  getRecordingStatus(): boolean {
    return this.isRecording
  }

  getSpeakingStatus(): boolean {
    return this.isSpeaking
  }

  // Check capabilities
  isAWSAvailable(): boolean {
    return !!this.pollyClient
  }

  isBrowserVoiceAvailable(): boolean {
    return !!(this.speechSynthesis && this.recognition)
  }

  getAvailableCapabilities(): {
    awsTranscribe: boolean
    awsPolly: boolean
    browserRecognition: boolean
    browserSynthesis: boolean
  } {
    return {
      awsTranscribe: awsTranscribeStreaming.isAvailable(),
      awsPolly: !!this.pollyClient,
      browserRecognition: !!this.recognition,
      browserSynthesis: !!this.speechSynthesis
    }
  }

  // Cleanup
  cleanup(): void {
    console.log("Cleaning up voice service")
    this.stopSpeaking()
    this.stopRecordingImmediate()
    
    // Stop AWS Transcribe streaming if active
    if (this.transcribeStopFunction) {
      console.log("🛑 Stopping AWS Transcribe during cleanup")
      this.transcribeStopFunction()
      this.transcribeStopFunction = null
    }
    
    // Clear any finalized transcript
    this.finalizeTranscript = ""
  }
}

// Singleton instance
let voiceServiceInstance: VoiceService | null = null

export const getVoiceService = (config?: VoiceConfig): VoiceService | null => {
  try {
    if (!voiceServiceInstance) {
      const finalConfig = config || {
        region: process.env.NEXT_PUBLIC_AWS_REGION || 'eu-central-1',
        identityPoolId: process.env.NEXT_PUBLIC_COGNITO_IDENTITY_POOL_ID || ''
      }
      
      console.log(`🎤 Creating new VoiceService instance for region: ${finalConfig.region}`)
      voiceServiceInstance = new VoiceService(finalConfig)
    }
    
    console.log('✅ VoiceService instance available:', !!voiceServiceInstance)
    return voiceServiceInstance
  } catch (error) {
    console.error('❌ Failed to get/create VoiceService:', error)
    console.error('🔧 Check that all required environment variables are set')
    return null
  }
}

export const initializeVoiceService = (config: VoiceConfig): VoiceService => {
  console.log('🎤 Force initializing VoiceService with config')
  voiceServiceInstance = new VoiceService(config)
  return voiceServiceInstance
}
