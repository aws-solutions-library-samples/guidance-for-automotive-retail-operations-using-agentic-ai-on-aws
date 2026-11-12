import {
  CognitoUserPool,
  CognitoUser,
  AuthenticationDetails,
  CognitoUserSession,
} from 'amazon-cognito-identity-js';
import { User, LoginCredentials, CognitoConfig } from '@/types/auth';

// Get configuration from environment variables with real credentials
const cognitoConfig: CognitoConfig = {
  userPoolId: process.env.NEXT_PUBLIC_COGNITO_USER_POOL_ID || 'REPLACE_WITH_YOUR_USER_POOL_ID',
  clientId: process.env.NEXT_PUBLIC_COGNITO_CLIENT_ID || 'REPLACE_WITH_YOUR_CLIENT_ID',
  region: process.env.NEXT_PUBLIC_AWS_REGION || 'us-east-1',
};

// Enterprise OIDC configuration
const ssoConfig = {
  clientId: process.env.NEXT_PUBLIC_SSO_CLIENT_ID || 'REPLACE_WITH_YOUR_CLIENT_ID',
  domain: process.env.NEXT_PUBLIC_COGNITO_DOMAIN || 'REPLACE_WITH_YOUR_COGNITO_DOMAIN',
  identityProvider: 'CorpDirectory',
};

console.log('Cognito Config:', cognitoConfig);

// Helper function to get or create user pool (lazy initialization)
let userPool: CognitoUserPool | null = null;

function getUserPool(): CognitoUserPool {
  if (!userPool) {
    // Only create if we're in browser and have valid config
    if (typeof window === 'undefined') {
      // During SSR/build, return a mock that won't be used
      return {} as CognitoUserPool;
    }
    
    userPool = new CognitoUserPool({
      UserPoolId: cognitoConfig.userPoolId,
      ClientId: cognitoConfig.clientId,
    });
  }
  return userPool;
}

export class CognitoService {
  static async signIn(credentials: LoginCredentials): Promise<User> {
    return new Promise((resolve, reject) => {
      const authenticationDetails = new AuthenticationDetails({
        Username: credentials.username,
        Password: credentials.password,
      });

      const cognitoUser = new CognitoUser({
        Username: credentials.username,
        Pool: getUserPool(),
      });

      cognitoUser.authenticateUser(authenticationDetails, {
        onSuccess: (session: CognitoUserSession) => {
          const idToken = session.getIdToken();
          const payload = idToken.payload;
          
          const user: User = {
            id: payload.sub,
            username: credentials.username,
            email: payload.email || '',
            role: payload['custom:role'] === 'administrator' ? 'administrator' : 'technician',
            firstName: payload.given_name || '',
            lastName: payload.family_name || '',
            isActive: true,
            createdAt: new Date().toISOString(),
            lastLogin: new Date().toISOString(),
          };

          // Store user data and session marker for persistence.
          // Use 'cognito-validated' marker (not the raw JWT) so AuthContext
          // correctly identifies this as a Cognito SDK session and uses
          // refresh tokens to renew expired access/ID tokens automatically.
          localStorage.setItem('vistaUser', JSON.stringify(user));
          localStorage.setItem('vistaAuthToken', 'cognito-validated');

          resolve(user);
        },
        onFailure: (err) => {
          console.error('Cognito authentication error:', err);
          if (err.code === 'NetworkError' || err.message?.includes('ERR_SOCKET_NOT_CONNECTED')) {
            reject(new Error('Network connection failed. Please check your internet connection and try again.'));
          } else {
            reject(new Error(err.message || 'AWS Cognito authentication failed'));
          }
        },
        newPasswordRequired: () => {
          reject(new Error('New password required'));
        },
      });
    });
  }

  static async signOut(): Promise<void> {
    return new Promise((resolve) => {
      const cognitoUser = getUserPool().getCurrentUser();
      if (cognitoUser) {
        cognitoUser.signOut();
      }
      localStorage.removeItem('vistaAuthToken');
      localStorage.removeItem('vistaUser');
      resolve();
    });
  }

  static async getCurrentUser(): Promise<User | null> {
    return new Promise((resolve) => {
      // Check if we're in browser environment (companion app pattern)
      if (typeof window === 'undefined') {
        console.log('Server-side rendering, skipping auth check');
        resolve(null);
        return;
      }

      // Check network connectivity (companion app pattern)
      if (!navigator.onLine) {
        console.warn('No network connectivity, skipping auth check');
        resolve(null);
        return;
      }

      // Check if Cognito is configured (companion app pattern)
      const userPoolId = process.env.NEXT_PUBLIC_COGNITO_USER_POOL_ID;
      const clientId = process.env.NEXT_PUBLIC_COGNITO_CLIENT_ID;
      
      if (!userPoolId || !clientId) {
        console.warn('Cognito not configured, skipping auth check');
        resolve(null);
        return;
      }

      // Add longer timeout like companion app (10 seconds)
      const timeout = setTimeout(() => {
        console.warn('Cognito getCurrentUser timeout, resolving with null');
        resolve(null);
      }, 10000); // 10 second timeout like companion app
      
      const cognitoUser = getUserPool().getCurrentUser();
      
      if (cognitoUser) {
        cognitoUser.getSession((err: any, session: CognitoUserSession) => {
          clearTimeout(timeout);
          
          if (err) {
            // Handle network errors gracefully like companion app
            if (err.message?.includes('Network error') || 
                err.message?.includes('ERR_SOCKET_NOT_CONNECTED') ||
                err.code === 'NetworkError') {
              console.warn('Network error during auth check, resolving with null');
              resolve(null);
              return;
            }
            console.log('Cognito session error:', err);
            resolve(null);
            return;
          }

          if (!session || !session.isValid()) {
            console.log('Cognito session invalid');
            resolve(null);
            return;
          }

          const idToken = session.getIdToken();
          const payload = idToken.payload;
          
          const user: User = {
            id: payload.sub,
            username: cognitoUser.getUsername(),
            email: payload.email || '',
            role: payload['custom:role'] === 'administrator' ? 'administrator' : 'technician',
            firstName: payload.given_name || '',
            lastName: payload.family_name || '',
            isActive: true,
            createdAt: new Date().toISOString(),
            lastLogin: new Date().toISOString(),
          };

          console.log('Cognito user session valid:', user.username);
          resolve(user);
        });
      } else {
        clearTimeout(timeout);
        console.log('No Cognito user found');
        resolve(null);
      }
    });
  }

  static async refreshSession(): Promise<boolean> {
    return new Promise((resolve) => {
      const cognitoUser = getUserPool().getCurrentUser();
      
      if (cognitoUser) {
        cognitoUser.getSession((err: any, session: CognitoUserSession) => {
          if (err || !session.isValid()) {
            resolve(false);
            return;
          }

          const refreshToken = session.getRefreshToken();
          cognitoUser.refreshSession(refreshToken, (refreshErr, newSession) => {
            if (refreshErr) {
              resolve(false);
            } else {
              resolve(true);
            }
          });
        });
      } else {
        resolve(false);
      }
    });
  }

  /**
   * Initiate enterprise SSO (OIDC) login by redirecting to Cognito Hosted UI
   */
  static initiateOIDCLogin(): void {
    if (typeof window === 'undefined') return;

    const region = cognitoConfig.region;
    const domain = ssoConfig.domain;
    const clientId = ssoConfig.clientId;
    const identityProvider = ssoConfig.identityProvider;
    
    // Determine callback URL based on current environment
    const callbackUrl = window.location.origin;
    
    // Build the Cognito Hosted UI URL with identity_provider parameter
    const hostedUIUrl = new URL(`https://${domain}.auth.${region}.amazoncognito.com/oauth2/authorize`);
    hostedUIUrl.searchParams.set('client_id', clientId);
    hostedUIUrl.searchParams.set('response_type', 'code');
    hostedUIUrl.searchParams.set('scope', 'email openid');
    hostedUIUrl.searchParams.set('redirect_uri', callbackUrl);
    hostedUIUrl.searchParams.set('identity_provider', identityProvider);
    
    console.log('🔐 Redirecting to enterprise SSO login:', hostedUIUrl.toString());
    
    // Redirect to Cognito Hosted UI
    window.location.href = hostedUIUrl.toString();
  }

  /**
   * Handle OAuth callback - exchange authorization code for tokens
   */
  static async handleOAuthCallback(code: string): Promise<User> {
    if (typeof window === 'undefined') {
      throw new Error('OAuth callback can only be handled in browser');
    }

    const region = cognitoConfig.region;
    const domain = ssoConfig.domain;
    const clientId = ssoConfig.clientId;
    const callbackUrl = window.location.origin;

    // Exchange authorization code for tokens
    const tokenUrl = `https://${domain}.auth.${region}.amazoncognito.com/oauth2/token`;
    
    const params = new URLSearchParams();
    params.append('grant_type', 'authorization_code');
    params.append('client_id', clientId);
    params.append('code', code);
    params.append('redirect_uri', callbackUrl);

    console.log('🔄 Exchanging authorization code for tokens...');

    const response = await fetch(tokenUrl, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/x-www-form-urlencoded',
      },
      body: params.toString(),
    });

    if (!response.ok) {
      const errorText = await response.text();
      console.error('Token exchange failed:', errorText);
      throw new Error(`Token exchange failed: ${response.status}`);
    }

    const tokens = await response.json();
    console.log('✅ Tokens received successfully');

    // Parse the ID token to get user info
    const idToken = tokens.id_token;
    const payload = JSON.parse(atob(idToken.split('.')[1]));

    const user: User = {
      id: payload.sub,
      username: payload.email || payload['cognito:username'] || payload.sub,
      email: payload.email || '',
      role: payload['custom:role'] === 'administrator' ? 'administrator' : 'technician',
      firstName: payload.given_name || payload.email?.split('@')[0] || '',
      lastName: payload.family_name || '',
      isActive: true,
      createdAt: new Date().toISOString(),
      lastLogin: new Date().toISOString(),
    };

    // Store tokens and user data
    localStorage.setItem('vistaUser', JSON.stringify(user));
    localStorage.setItem('vistaAuthToken', idToken);
    localStorage.setItem('vistaAccessToken', tokens.access_token);
    if (tokens.refresh_token) {
      localStorage.setItem('vistaRefreshToken', tokens.refresh_token);
    }

    console.log('✅ SSO user authenticated:', user.username);
    return user;
  }

  /**
   * Check if there's an OAuth callback code in the URL
   */
  static getOAuthCallbackCode(): string | null {
    if (typeof window === 'undefined') return null;
    
    const urlParams = new URLSearchParams(window.location.search);
    return urlParams.get('code');
  }

  /**
   * Clear OAuth callback code from URL without page reload
   */
  static clearOAuthCallbackCode(): void {
    if (typeof window === 'undefined') return;
    
    const url = new URL(window.location.href);
    url.searchParams.delete('code');
    url.searchParams.delete('state');
    window.history.replaceState({}, document.title, url.pathname);
  }

}
