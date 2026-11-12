/**
 * Shared Authentication Utilities
 * Centralized JWT token retrieval for AWS SDK clients
 */

const AWS_REGION = process.env.NEXT_PUBLIC_AWS_REGION || 'us-east-1';

/**
 * Get JWT token from current auth session (enterprise SSO OAuth or Cognito SDK)
 *
 * This function checks multiple auth sources in order:
 * 1. Stored enterprise SSO OAuth token from localStorage
 * 2. Cognito SDK session token (for username/password login)
 *
 * @returns JWT token string or null if no valid session
 */
export async function getJwtToken(): Promise<string | null> {
  // Check for stored token (enterprise SSO OAuth)
  if (typeof window !== 'undefined') {
    const storedToken = localStorage.getItem('vistaAuthToken');
    if (storedToken && storedToken !== 'cognito-validated' && storedToken !== 'mock-token') {
      return storedToken;
    }
  }

  // Fall back to Cognito SDK session (username/password login)
  try {
    const { CognitoUserPool } = await import('amazon-cognito-identity-js');

    const userPoolId = process.env.NEXT_PUBLIC_COGNITO_USER_POOL_ID;
    const clientId = process.env.NEXT_PUBLIC_COGNITO_CLIENT_ID;

    if (!userPoolId || !clientId) {
      return null;
    }

    const userPool = new CognitoUserPool({
      UserPoolId: userPoolId,
      ClientId: clientId,
    });

    const currentUser = userPool.getCurrentUser();
    if (!currentUser) return null;

    return new Promise((resolve) => {
      // getSession automatically refreshes expired tokens using the refresh token
      currentUser.getSession((err: Error | null, session: { isValid: () => boolean; getIdToken: () => { getJwtToken: () => string } } | null) => {
        if (err || !session) {
          // Session completely invalid — user needs to log in again
          resolve(null);
        } else {
          resolve(session.getIdToken().getJwtToken());
        }
      });
    });
  } catch {
    return null;
  }
}

/**
 * Get Cognito Identity Pool login configuration for AWS SDK credentials
 *
 * @param token - JWT token from getJwtToken()
 * @returns Login configuration object for fromCognitoIdentityPool
 */
export function getCognitoLogins(token: string): Record<string, string> {
  const userPoolId = process.env.NEXT_PUBLIC_COGNITO_USER_POOL_ID;
  return {
    [`cognito-idp.${AWS_REGION}.amazonaws.com/${userPoolId}`]: token,
  };
}

/**
 * Get Identity Pool ID from environment
 */
export function getIdentityPoolId(): string {
  return process.env.NEXT_PUBLIC_COGNITO_IDENTITY_POOL_ID || '';
}

/**
 * Get AWS Region from environment
 */
export function getAwsRegion(): string {
  return AWS_REGION;
}
