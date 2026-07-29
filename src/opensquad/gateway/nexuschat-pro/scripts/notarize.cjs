/**
 * electron-builder afterSign hook — notarize macOS builds when Apple
 * credentials are present. Skips cleanly for unsigned / local builds.
 *
 * Required env (Developer ID + app-specific password flow):
 *   APPLE_ID
 *   APPLE_APP_SPECIFIC_PASSWORD
 *   APPLE_TEAM_ID
 *
 * Or App Store Connect API key flow:
 *   APPLE_API_KEY_ID
 *   APPLE_API_KEY (path to .p8, or contents)
 *   APPLE_API_ISSUER
 */
'use strict'

exports.default = async function notarizeMacApp(context) {
  const { electronPlatformName, appOutDir, packager } = context
  if (electronPlatformName !== 'darwin') return

  const appleId = process.env.APPLE_ID
  const appleIdPassword = process.env.APPLE_APP_SPECIFIC_PASSWORD
  const teamId = process.env.APPLE_TEAM_ID
  const apiKeyId = process.env.APPLE_API_KEY_ID
  const apiKey = process.env.APPLE_API_KEY
  const apiIssuer = process.env.APPLE_API_ISSUER

  const hasPasswordAuth = Boolean(appleId && appleIdPassword && teamId)
  const hasApiKeyAuth = Boolean(apiKeyId && apiKey && apiIssuer)

  if (!hasPasswordAuth && !hasApiKeyAuth) {
    console.log(
      '[notarize] Skipping — set APPLE_ID + APPLE_APP_SPECIFIC_PASSWORD + APPLE_TEAM_ID ' +
        '(or APPLE_API_KEY_ID + APPLE_API_KEY + APPLE_API_ISSUER) to enable notarization.',
    )
    return
  }

  // Prefer the copy shipped with electron-builder; fall back to a direct dep if present.
  let notarize
  try {
    ;({ notarize } = require('@electron/notarize'))
  } catch {
    console.warn('[notarize] @electron/notarize not found — skip')
    return
  }

  const appName = packager.appInfo.productFilename
  const appPath = `${appOutDir}/${appName}.app`

  console.log(`[notarize] Submitting ${appPath} …`)

  /** @type {Record<string, string>} */
  const opts = { appPath }
  if (hasApiKeyAuth) {
    opts.appleApiKey = apiKey
    opts.appleApiKeyId = apiKeyId
    opts.appleApiIssuer = apiIssuer
  } else {
    opts.appleId = appleId
    opts.appleIdPassword = appleIdPassword
    opts.teamId = teamId
  }

  await notarize(opts)
  console.log('[notarize] Done')
}
